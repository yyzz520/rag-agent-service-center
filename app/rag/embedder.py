"""Embedding 客户端：在 app.ai.providers 的客户端外面套一层 Redis 缓存。

只缓 embed_query（检索时对问句/改写查询求向量）：同一问句反复问、或改写出多个变体里
重复出现的短语，都能省一次 embedding API 调用。embed_documents 不缓——它只在入库时
被调一次，缓存没有收益，反而会把整库向量都塞进 Redis。

key 是文本的纯函数、结果不含任何用户数据，所以与租户隔离无关，可以全局共享。

换供应商会让已向量化的旧块失效（需重建向量库），故不做供应商级 fallback。
"""
from functools import lru_cache

from langchain_core.embeddings import Embeddings

from app.ai.providers import get_embeddings as _get_embeddings
from app.rag import cache


class CachedEmbeddings(Embeddings):
    def __init__(self, inner: Embeddings) -> None:
        self._inner = inner

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._inner.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        hit = cache.get_embedding(text)
        if hit is not None:
            return hit
        vec = self._inner.embed_query(text)
        cache.set_embedding(text, vec)
        return vec


@lru_cache
def get_embeddings() -> CachedEmbeddings:
    return CachedEmbeddings(_get_embeddings())
