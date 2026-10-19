from datetime import date, datetime
from pathlib import Path

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.documents import Document
from openpyxl import load_workbook
from pypdf import PdfReader

# 整份 PDF 提取到的文字层字符数低于该阈值 => 判定扫描/图像 PDF（走 MinerU OCR）。
# 文字层 PDF 阈值低：正常文档几乎不会整份连 10 个字符都没有。
_SCANNED_TEXT_MAX = 10


def load_document(path: Path) -> list[Document]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _load_pdf(path)
    if suffix == ".docx":
        return [_load_docx(path)]
    if suffix == ".xlsx":
        return [_load_xlsx(path)]
    if suffix in (".md", ".txt"):
        return TextLoader(str(path), encoding="utf-8").load()
    raise ValueError(f"不支持的格式: {suffix}（仅支持 .txt .md .pdf .docx .xlsx）")


def _load_pdf(path: Path) -> list[Document]:
    """PDF：优先吃文字层（快、免费）。整份无文字 => 扫描/图像件，交 MinerU OCR。"""
    probe = _extract_text_probe(path)
    if len(probe.strip()) > _SCANNED_TEXT_MAX:
        return PyPDFLoader(str(path)).load()  # 文字层 PDF，逐页一个 Document（分块天然按页切）
    return _load_scanned_pdf(path)


def _extract_text_probe(path: Path) -> str:
    with open(path, "rb") as f:
        reader = PdfReader(f)
        return "".join(page.extract_text() or "" for page in reader.pages)


def _load_scanned_pdf(path: Path) -> list[Document]:
    """扫描/图像 PDF → MinerU Cloud OCR。未配 MINERU_API_KEY 时报明确错误。"""
    from app.config import settings

    if not settings.mineru_api_key.strip():
        raise ValueError(
            f"文件为扫描/图像 PDF（无文字层），需在 .env 配置 MINERU_API_KEY"
            "（MinerU Cloud OCR）后才能识别；未配置已跳过"
        )
    from app.services.mineru import ocr_pdf_to_text

    text = ocr_pdf_to_text(path)
    return [Document(page_content=text)]


def _load_docx(path: Path) -> Document:
    """Word → 文本：按正文顺序抽段落与表格，合并单元格去重，空白规整。"""
    doc = DocxDocument(str(path))
    parts: list[str] = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            text = DocxParagraph(child, doc).text.strip()
        elif child.tag == qn("w:tbl"):
            text = _docx_table_text(DocxTable(child, doc))
        else:
            continue
        if text:
            parts.append(text)
    return Document(page_content="\n".join(parts))


def _docx_table_text(table: DocxTable) -> str:
    lines = []
    for row in table.rows:
        cells = []
        seen = set()
        for c in row.cells:  # 合并单元格会对同一 cell 重复返回，按 id 去重
            if id(c) in seen:
                continue
            seen.add(id(c))
            cells.append(" ".join(c.text.split()))
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _load_xlsx(path: Path) -> Document:
    """Excel → 文本：逐工作表，标题行打头，逐行 "A | B | C" 拼文本行。

    日期统一格式化为 YYYY-MM-DD，避免 datetime 完整时间戳污染检索文本。
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    parts: list[str] = []
    try:
        for ws in wb.worksheets:
            parts.append(f"工作表：{ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = [_cell_text(v) for v in row]
                cells = [c for c in cells if c]
                if cells:
                    parts.append(" | ".join(cells))
    finally:
        wb.close()
    return Document(page_content="\n".join(parts))


def _cell_text(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    return " ".join(str(v).split())
