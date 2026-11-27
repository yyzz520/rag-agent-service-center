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
        raw = get_vectorstore().get(ids=parent_ids, include=["documents", "metadatas"])
        contents = raw.get("documents") or []
        metas = raw.get("metadatas")
        if metas is None:  # 兼容只返回 documents 的 store/测试桩
            metas = [None] * len(contents)
        parents = dict(zip(raw.get("ids", []), zip(contents, metas)))
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
        hit = parents.get(pid)
        if hit:
            content, parent_meta = hit
            # 防御：父块若不属于同一个 doc_id（数据污染/恶意 parent_id），退回子块，
            # 不让跨文档/跨租户的内容通过 parent 扩展混进上下文。
            child_doc = d.metadata.get("doc_id")
            parent_doc = (parent_meta or {}).get("doc_id")
            if child_doc and parent_doc and child_doc != parent_doc:
                out.append(d)
                continue
            meta = dict(d.metadata)
            meta["chunk_level"] = "parent"
            meta["parent_id"] = pid
            meta["chunk_id"] = (parent_meta or {}).get("chunk_id") or pid
            out.append(Document(page_content=content, metadata=meta))
            seen.add(pid)
        else:
            out.append(d)
    return out
