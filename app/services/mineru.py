"""MinerU Cloud OCR v4 客户端：扫描/图像 PDF 上传解析，返回纯文本。

只做一件事：本地文件 → 上传 → 轮询 → 下载 zip → 取 Markdown 拼文本。
调用方是 app.rag.loader（loader 探到 PDF 无文字层才走这里），后台任务线程池里跑。

合同要点（mineru.net/apiManage/docs，v4.0.0）：
- 鉴权：Header `Authorization: Bearer <MINERU_API_KEY>`
- 成功响应 `{"code":0,"data":{...},"msg":"ok"}`；鉴权失败 `{"success":false,"msgCode":"A0202",...}`
- 本地上传：POST /file-urls/batch → data.batch_id + data.file_urls[0]（预签名直传 URL）
- 直传：PUT 字节到该 URL
- 轮询：GET /extract-results/batch/{batch_id} → data.state 到 done → data.full_zip_url
- 结果 zip：内含 Markdown + JSON（+可选 docx/html/latex），OCR 文本取 *.md
限：单文件 ≤200MB / ≤200 页；每日高优先级 1000 页配额；Office 会切 pipeline。
"""
import io
import time
import zipfile
from pathlib import Path
from typing import Optional

import httpx

from app.config import settings

_POLL_INTERVAL_SEC = 3.0
_DONE_STATES = {"done", "success", "succeed"}


class MinerUError(RuntimeError):
    """MinerU OCR 失败，message 已带上可读原因/错误码，直接进 doc.error_msg。"""


def ocr_pdf_to_text(path: Path, *, data_id: Optional[str] = None) -> str:
    """扫描 PDF → OCR 纯文本。返回整份 Markdown 文本（多页拼接）。"""
    api_key = settings.mineru_api_key.strip()
    if not api_key:
        raise MinerUError("扫描/图像 PDF 需配置 MINERU_API_KEY（MinerU Cloud OCR），当前未配置")

    file_name = Path(path).name
    data_id = data_id or file_name
    batch_id, upload_url = _create_upload(file_name, data_id)
    _put_file(upload_url, path)
    zip_bytes = _poll_until_done(batch_id, data_id)
    return _extract_markdown(zip_bytes, file_name)


# ---------------- 内部步骤 ----------------

def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "accept": "application/json",
    }


def _require_data(resp: httpx.Response, what: str) -> dict:
    """按 MinerU 成功/失败两种响应形状取 data；失败抛带错误码的 MinerUError。"""
    try:
        payload = resp.json()
    except ValueError:
        raise MinerUError(f"MinerU {what} 返回非 JSON：HTTP {resp.status_code} {resp.text[:200]}")
    if isinstance(payload, dict) and payload.get("success") is False:
        code = payload.get("msgCode", payload.get("code", "?"))
        msg = payload.get("msg", payload.get("error_message", resp.text[:200]))
        raise MinerUError(f"MinerU {what} 失败 [{code}]：{msg}")
    if payload.get("code") == 0 and isinstance(payload.get("data"), dict):
        return payload["data"]
    code = payload.get("code", "?")
    msg = payload.get("msg", payload.get("error_message", resp.text[:300]))
    raise MinerUError(f"MinerU {what} 失败 [code={code}]：{msg}")


def _create_upload(file_name: str, data_id: str) -> tuple[str, str]:
    url = f"{settings.mineru_base_url}/file-urls/batch"
    body = {
        "files": [{"name": file_name, "data_id": data_id[:128]}],
        "model_version": settings.mineru_model_version,
        "is_ocr": True,  # 扫描件必须；文字层 PDF 不会走到这里
        "enable_formula": True,
        "enable_table": True,
        "language": "ch",
    }
    with httpx.Client(timeout=60) as client:
        resp = client.post(url, headers=_headers(settings.mineru_api_key), json=body)
    if resp.status_code != 200:
        raise MinerUError(f"MinerU 获取上传链接 HTTP {resp.status_code}：{resp.text[:200]}")
    data = _require_data(resp, "获取上传链接")
    batch_id = data.get("batch_id")
    urls = data.get("file_urls") or []
    if not batch_id or not urls:
        raise MinerUError(f"MinerU 返回缺少 batch_id/file_urls：{data}")
    return batch_id, urls[0]


def _put_file(upload_url: str, path: Path) -> None:
    # 预签名 URL 的签名不含 Content-Type：带任何非签名头部都会被 OSS 拒（403
    # SignatureDoesNotMatch），所以必须"裸 PUT"，格式由 file_urls 的 URL 路径决定。
    content = path.read_bytes()
    with httpx.Client(timeout=300) as client:
        resp = client.put(upload_url, content=content)
    if resp.status_code not in (200, 201, 204):
        raise MinerUError(f"MinerU 文件直传失败 HTTP {resp.status_code}：{resp.text[:200]}")


def _poll_until_done(batch_id: str, data_id: str) -> bytes:
    """轮询批量结果，按 data_id 找到本文件的那条 extract_result。

    v4 返回结构是 data.extract_result[]（批量可含多文件，逐文件 state），不是
    顶层 data.state。state=done 时该条带 full_zip_url。
    """
    url = f"{settings.mineru_base_url}/extract-results/batch/{batch_id}"
    deadline = time.monotonic() + max(settings.mineru_ocr_timeout, 60)
    with httpx.Client(timeout=60) as client:
        while True:
            resp = client.get(url, headers=_headers(settings.mineru_api_key))
            if resp.status_code != 200:
                raise MinerUError(f"MinerU 查询任务 HTTP {resp.status_code}：{resp.text[:200]}")
            data = _require_data(resp, "查询任务")
            entry = _find_entry(data, data_id)
            if entry is None:
                if time.monotonic() >= deadline:
                    raise MinerUError(f"MinerU 结果里找不到本文件条目（batch={batch_id}，data_id={data_id}）")
                time.sleep(_POLL_INTERVAL_SEC)
                continue
            state = (entry.get("state") or "").lower()
            if state in _DONE_STATES:
                return _download_zip(entry.get("full_zip_url"), batch_id, client)
            if state == "failed":
                err = entry.get("err_msg") or entry.get("error_message") or entry
                raise MinerUError(f"MinerU 解析失败：{err}")
            if time.monotonic() >= deadline:
                raise MinerUError(f"MinerU 解析超时（>{settings.mineru_ocr_timeout}s），最后状态：{state or 'pending'}")
            time.sleep(_POLL_INTERVAL_SEC)


def _find_entry(data: dict, data_id: str) -> Optional[dict]:
    """extract_result 数组里按 data_id/file_name 匹配本文件条目（批量多文件区分用）。"""
    entries = data.get("extract_result")
    if not isinstance(entries, list):
        return None
    if not entries:
        return None
    for e in entries:
        if isinstance(e, dict) and e.get("data_id") == data_id:
            return e
    return entries[0] if isinstance(entries[0], dict) else None  # 单文件批量：没匹配也取第一条


def _download_zip(zip_url: Optional[str], batch_id: str, client: httpx.Client) -> bytes:
    if not zip_url:
        raise MinerUError(f"MinerU 任务完成但缺少 full_zip_url（batch={batch_id}）")
    resp = client.get(zip_url)
    if resp.status_code != 200:
        raise MinerUError(f"MinerU 结果下载失败 HTTP {resp.status_code}")
    return resp.content


def _extract_markdown(zip_bytes: bytes, file_name: str) -> str:
    """zip 里取 *.md 文件拼全文（auto_split 可能拆多份，按文件名排序拼接）。"""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            md = sorted(n for n in names if n.lower().endswith(".md"))
            if not md:
                raise MinerUError(f"MinerU 结果 zip 无 Markdown 文件：{names[:10]}")
            parts = [zf.read(n).decode("utf-8", errors="replace").strip() for n in md]
    except zipfile.BadZipFile as e:
        raise MinerUError(f"MinerU 结果不是有效 zip：{e}")
    text = "\n\n".join(p for p in parts if p).strip()
    if not text:
        raise MinerUError(f"MinerU OCR 结果为空（{file_name}，md 文件 {len(md)} 个）")
    return text
