"""父子块上下文扩展：检索命中子块后，按 parent_id 取父块内容补上下文。

检索/评测仍然看子块（细粒度、指标稳定）；真正喂给 LLM 和返回 sources 的是父块，
避免模型只看到半段话。父块去重，多个子块命中同一父块只拼一次。
"""
from langchain_core.documents import Document

from app.rag.vector_store import get_vectorstore


def expand_parents(docs: list[Document]) -> list[Document]:
    parent_ids = sorted({
        d.metadata["parent_id"]
        for d in docs
        if d.metadata.get("parent_id")
    })
    if not parent_ids:
        return docs
    try:
        raw = get_vectorstore().get(ids=parent_ids, include=["documents"])
        parents = dict(zip(raw.get("ids", []), raw.get("documents", [])))
    except Exception:
        return docs

    out: list[Document] = []
    seen: set[str] = set()
    for d in docs:
        if d.metadata.get("chunk_level") == "parent":
            out.append(d)
            continue
        pid = d.metadata.get("parent_id")
        if not pid:
            out.append(d)
            continue
        if pid in seen:
            continue
        content = parents.get(pid)
        if content:
            meta = dict(d.metadata)
            meta["chunk_level"] = "parent"
            meta["parent_id"] = pid
            out.append(Document(page_content=content, metadata=meta))
            seen.add(pid)
        else:
            out.append(d)
    return out
