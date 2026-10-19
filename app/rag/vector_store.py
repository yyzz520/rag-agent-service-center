from functools import lru_cache

from langchain_chroma import Chroma
from langchain_core.documents import Document

from app.config import settings
from app.rag.embedder import get_embeddings


@lru_cache
def get_vectorstore() -> Chroma:
    """Chroma 向量库单例。两种部署形态，只由 settings.chroma_host 决定：

    - 空（默认）：嵌入式 + 文件持久化，重启不丢，零基础设施一键跑；
    - 填 host/port：连独立 Chroma server。这是 `--scale app=N` 的前置条件——
      嵌入式库是进程内独占的文件，多个副本各自持有会互相踩、检索结果互相看不见。

    两种形态都走客户端算向量：Chroma server 不负责 embedding，embedding_function
    必须照传，否则检索会退化成"用别的模型算的向量去比"，结果无意义。

    块元数据关键键：source（展示/溯源路径）、page、doc_id（uuid）、user_id、kb_id。
    多租户隔离 = 检索时 Chroma where 按 doc_id 过滤（决策4），不拆 collection；
    入库前调用方必须在每个块上打好这些键。
    """
    common = {
        "embedding_function": get_embeddings(),
        "collection_name": "knowledge_base",
    }
    if settings.chroma_host:
        return Chroma(
            host=settings.chroma_host, port=settings.chroma_port, **common
        )
    return Chroma(persist_directory=settings.persist_dir, **common)


def add_documents(docs: list[Document]) -> int:
    ids = get_vectorstore().add_documents(docs)
    return len(ids)


def get_retriever(k: int = 5, where: dict | None = None):
    """构造检索器。where 是 Chroma 元数据过滤（如 {"doc_id": {"$in": [...]}}）。"""
    kwargs: dict = {"k": k}
    if where:
        kwargs["filter"] = where
    return get_vectorstore().as_retriever(search_kwargs=kwargs)


def delete_by_doc_id(doc_id: str) -> int:
    """按文档 uuid 清向量块（删除文档 / 解析失败清半份用）。返回清了几块。"""
    ids = get_vectorstore().get(where={"doc_id": doc_id}, include=[])["ids"]
    if ids:
        get_vectorstore().delete(ids=ids)
    return len(ids)


def count_chunks() -> int:
    try:
        return len(get_vectorstore().get()["ids"])
    except Exception:
        return 0
