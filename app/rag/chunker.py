"""结构感知 + 父子块切分。

- 有标题的文档：按 Markdown `#` / 中文“第 X 章/节”/编号标题切 section，每个 section 建父块；
- 父块内部再切 500 字左右的子块，子块带 `parent_id`；
- 表格行按整块处理，尽量不在表中间切断；超长表格按行切但重复表头；
- 没有标题的文档（PDF 文字层/扫描件）退回递归切分，父块=该页/整篇。

检索只召回 `chunk_level=child`；命中后由 context.expand_parents 取父块补上下文。
"""
import hashlib
import re

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_PLAIN_RE = re.compile(r"^\s*(第[一二三四五六七八九十百]+[章节部分])\s+\S")
_TABLE_HINT = re.compile(r"\s\|\s")

_PARENT_SIZE = 6000
_CHILD_SIZE = 700
_CHILD_OVERLAP = 100

_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]


def _parent_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=_PARENT_SIZE, chunk_overlap=200, separators=_SEPARATORS
    )


def _child_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=_CHILD_SIZE, chunk_overlap=_CHILD_OVERLAP, separators=_SEPARATORS
    )


def _is_table_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") or (s.count("|") >= 2 and bool(_TABLE_HINT.search(s)))


def _heading(line: str) -> tuple[int, str] | None:
    m = _HEADING_RE.match(line)
    if m:
        return len(m.group(1)), m.group(2).strip()
    s = line.strip()
    if not s or len(s) > 60 or s.endswith("。") or "|" in s:
        return None
    m = _PLAIN_RE.match(line)
    if not m:
        return None
    label = m.group(1)
    level = 2 if "节" in label else 1
    return min(max(level, 1), 6), s


def _sections(text: str) -> list[tuple[str, str, str]]:
    """返回 [(section_title, section_path, body)]。"""
    sections: list[tuple[str, str, str]] = []
    path: list[str] = []
    title = ""
    body: list[str] = []

    def flush() -> None:
        nonlocal body
        if any(line.strip() for line in body):
            sections.append((title, " / ".join(path), "\n".join(body).strip()))
        body = []

    for line in text.splitlines():
        h = _heading(line)
        if h is None:
            body.append(line)
            continue
        flush()
        level, title = h
        path = path[: level - 1]
        while len(path) < level - 1:
            path.append("")
        path.append(title)
    flush()
    if not sections:
        sections.append(("", "", text.strip()))
    return sections


def _blocks(text: str) -> list[tuple[str, str]]:
    """把段落切成 (kind, text)，kind=table/text；表格连续行整体保留。"""
    blocks: list[tuple[str, str]] = []
    buf: list[str] = []
    table: list[str] = []

    def flush_buf() -> None:
        nonlocal buf
        if any(x.strip() for x in buf):
            blocks.append(("text", "\n".join(buf).strip()))
        buf = []

    def flush_table() -> None:
        nonlocal table
        if table:
            blocks.append(("table", "\n".join(table).strip()))
        table = []

    for line in text.splitlines():
        if _is_table_line(line):
            flush_buf()
            table.append(line)
        else:
            flush_table()
            buf.append(line)
    flush_buf()
    flush_table()
    return blocks


def _table_children(table: str, splitter: RecursiveCharacterTextSplitter) -> list[str]:
    if len(table) <= _CHILD_SIZE:
        return [table]
    rows = table.splitlines()
    header = rows[0] if rows else ""
    out: list[str] = []
    buf: list[str] = []
    for row in rows[1:]:
        candidate = "\n".join(([header] if header else []) + buf + [row])
        if buf and len(candidate) > _CHILD_SIZE:
            out.append("\n".join(([header] if header else []) + buf))
            buf = [row]
        else:
            buf.append(row)
    if buf:
        out.append("\n".join(([header] if header else []) + buf))
    return out or splitter.split_text(table)


def _child_texts(parent_text: str, title: str) -> list[str]:
    splitter = _child_splitter()
    out: list[str] = []
    for kind, block in _blocks(parent_text):
        if kind == "table":
            chunks = _table_children(block, splitter)
        else:
            chunks = splitter.split_text(block)
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            if title and not chunk.startswith(title):
                chunk = f"{title}\n{chunk}"
            out.append(chunk)
    return out


def _parent_id(meta: dict, path: str, index: int, text: str) -> str:
    raw = f"{meta.get('doc_id', '')}|{path}|{index}|{text[:160]}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def chunk_documents(docs: list[Document]) -> list[Document]:
    """把 load_document 的结果切成 parent + child 两层。"""
    out: list[Document] = []
    parent_splitter = _parent_splitter()
    for doc in docs:
        base = dict(doc.metadata)
        for title, path, body in _sections(doc.page_content):
            parent_text = f"{title}\n{body}".strip() if title else body.strip()
            if not parent_text:
                continue
            parents = (
                parent_splitter.split_text(parent_text)
                if len(parent_text) > _PARENT_SIZE
                else [parent_text]
            )
            for pi, ptext in enumerate(parents):
                pid = _parent_id(base, path, pi, ptext)
                out.append(Document(
                    page_content=ptext,
                    metadata={
                        **base,
                        "chunk_level": "parent",
                        "parent_id": pid,
                        "section_title": title,
                        "section_path": path,
                    },
                ))
                for child in _child_texts(ptext, title):
                    out.append(Document(
                        page_content=child,
                        metadata={
                            **base,
                            "chunk_level": "child",
                            "parent_id": pid,
                            "section_title": title,
                            "section_path": path,
                        },
                    ))
    return out
