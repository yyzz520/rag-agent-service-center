"""文档入库：存文件 → 切块打元数据 → 写向量 → doc 状态落库。

load_document / get_splitter 即改造计划里"可插拔解析引擎"的 builtin 主路径；
M2 加 mineru_cloud / mineru_remote 时只换这里，不进路由层。
后台任务解析不需要 user 上下文：doc 行已存 owner，worker 按 doc_id 取数即可（6.4）。
"""
import io
import re
import zipfile
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.models import Doc
from app.rag.loader import load_document
from app.rag.splitter import get_splitter
from app.rag.vector_store import add_documents, delete_by_doc_id

ALLOWED_SUFFIX = {".txt", ".md", ".pdf", ".docx", ".xlsx"}

# OOXML 容器必须含的签名条目：能区分真 Word/Excel，也能挡下"改了后缀的 zip/二进制"
OOXML_MEMBER = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml"}


def sanitize_file_name(name: str) -> str:
    """防路径穿越 + 危险字符消毒（M1 基本安全；MIME 嗅探 M3 补）。"""
    base = Path(name or "").name.strip()
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", base)
    if not base:
        raise ValueError("文件名不能为空")
    if Path(base).suffix.lower() not in ALLOWED_SUFFIX:
        raise ValueError(f"不支持的格式（仅支持 .txt .md .pdf .docx .xlsx）：{name}")
    return base


def sniff_content(file_name: str, content: bytes) -> None:
    """MIME 内容嗅探：不信扩展名，按内容校验（M3 上传加固）。

    名字是 txt/md/pdf/docx/xlsx 但内容不符（PDF 缺 %PDF 头、文本不可解码、
    二进制冒充、Office 非 ZIP 或缺 OOXML 签名条目）直接拒绝，在解析前就挡在门外。
    校验失败抛 ValueError，由路由层转 40001。
    """
    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf":
        if not content.startswith(b"%PDF"):
            raise ValueError("文件内容不是有效的 PDF（缺少 %PDF 文件头）")
        return
    if suffix in OOXML_MEMBER:
        _sniff_ooxml(suffix, content)
        return
    if b"\x00" in content:  # 文本里混 NUL 基本就是二进制冒充
        raise ValueError("文件含 NUL 字节，不是纯文本")
    for enc in ("utf-8", "gb18030"):
        try:
            content.decode(enc)
            return
        except UnicodeDecodeError:
            continue
    raise ValueError("文本文件无法解码（请保存为 UTF-8 或 GBK 编码）")


def _sniff_ooxml(suffix: str, content: bytes) -> None:
    """.docx/.xlsx = ZIP 容器，校验魔数与内部签名条目，防"改后缀的 zip/二进制"冒名。"""
    if not (content.startswith(b"PK\x03\x04") or content.startswith(b"PK\x05\x06")):
        raise ValueError(f"{suffix} 不是有效的 Office 文档（缺少 ZIP 文件头）")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = {n.lower() for n in zf.namelist()}
            # 个别生成器把 workbook.xml 放非标准前缀下，放宽到"含该条目"
            found = any(n.endswith(OOXML_MEMBER[suffix]) for n in names)
    except (zipfile.BadZipFile, OSError) as e:
        raise ValueError(f"{suffix} 内容损坏（ZIP 无法读取：{e}）")
    if not found:
        raise ValueError(f"文件内容不是有效的 Office 文档（缺少 {OOXML_MEMBER[suffix]}）")


def save_upload_file(doc: Doc, content: bytes) -> Path:
    """存到 uploads/{owner}/{doc_id}/{file_name}。doc_id 随机，天然不冲突、不覆盖。"""
    d = Path(settings.upload_dir) / doc.owner_id / doc.id
    d.mkdir(parents=True, exist_ok=True)
    path = d / doc.file_name
    path.write_bytes(content)
    return path


def delete_doc_files(doc: Doc) -> None:
    """删除 doc 对应的 uploads 源文件与 doc_id 目录（忽略权限类异常，别让删文件失败影响删 doc）。"""
    try:
        p = Path(doc.storage_path)
        if p.exists():
            p.unlink()
        # rmdir 只在空目录上成功，非空/非目录都会抛 OSError 被下面吞掉，不会误删别的文件
        p.parent.rmdir()
    except OSError:
        pass


def parse_and_index(doc_id: str) -> None:
    """后台任务：load → split → 打元数据 → 写 Chroma → doc 置 ready / failed。

    任一步失败：清掉已写入的半份向量 + status=failed + error_msg，不留脏数据（6.9#6）。
    """
    db = SessionLocal()
    try:
        doc = db.get(Doc, doc_id)
        if doc is None or doc.status != "parsing":
            return  # 已被删除或状态已推进
        path = Path(doc.storage_path)
        chunks = get_splitter().split_documents(load_document(path))
        for c in chunks:
            meta = {
                "source": doc.storage_path,  # 展示/溯源用；检索隔离看 doc_id，不看它
                "doc_id": doc.id,
                "user_id": doc.owner_id,
                "kb_id": doc.kb_id,
            }
            page = c.metadata.get("page")
            if page is not None:  # Chroma 不接受 None 元数据值
                meta["page"] = page
            c.metadata = meta
        n = add_documents(chunks)
        doc.status = "ready"
        doc.chunk_count = n
        doc.error_msg = None
        db.commit()
    except Exception as e:
        db.rollback()
        delete_by_doc_id(doc_id)  # 半份向量清掉，不留脏数据
        doc = db.get(Doc, doc_id)
        if doc is not None and doc.status == "parsing":
            doc.status = "failed"
            doc.error_msg = f"{type(e).__name__}: {e}"[:500]
            db.commit()
    finally:
        db.close()
