"""检索链路的缓存层（Redis）：按"是否与租户隔离有关"分两类，key 的口径完全不同。

- 隔离无关层（查询改写、query embedding）：结果是文本的纯函数，不含任何用户数据，
  key 只跟文本有关 → 跨用户共享，命中率高，缓存一份全局受益。
- 隔离敏感层（最终检索结果）：结果只含调用者可见的那些文档，key 必须带上 allowed_doc_ids
  作用域。**少带这个作用域就是跨租户串数据**（A 的缓存被别人读到），属于严重越权，
  所以作用域进 key 是硬要求，不是优化。

Redis 未配置 / 连不上时全部旁路（get 返回 None、set 丢弃），检索链路照常跑，
只是退回每次都真算——缓存是纯粹的省，不能变成正确性的单点。
"""
import json
import logging
import struct
import time
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import md5

from langchain_core.documents import Document

from app.config import settings

logger = logging.getLogger("app.rag.cache")

_RETRY_COOLDOWN = 30.0  # Redis 挂后隔多久再探，避免每次检索都等一遍连接超时

# 整层旁路开关：评测重放时打开，让改写/embedding/检索三处缓存一起失效。
# 用 ContextVar 而非全局开关：同一进程里评测线程旁路，别的请求照常吃缓存。
_bypass: ContextVar[bool] = ContextVar("cache_bypass", default=False)


@contextmanager
def bypass():
    """临时关掉整层缓存。评测必须真跑整条链路，命中缓存会让分数虚高且不可复现。"""
    token = _bypass.set(True)
    try:
        yield
    finally:
        _bypass.reset(token)


class _RedisCache:
    """同步 Redis 客户端（检索链路是同步的，工具跑在线程池里阻塞一下可接受）。

    与 app/limiter.py 各持一个客户端：那边在 async 中间件里必须用 redis.asyncio，
    这边在同步检索链路里，共用反而要来回切线程。
    """

    def __init__(self) -> None:
        self._redis = None
        self._retry_at = 0.0

    def _enabled(self) -> bool:
        return settings.cache_enabled and bool(settings.redis_url) and not _bypass.get()

    def _client(self):
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(settings.redis_url)
        return self._redis

    def get(self, key: str):
        if not self._enabled() or time.time() < self._retry_at:
            return None
        try:
            return self._client().get(key)
        except Exception as e:
            self._retry_at = time.time() + _RETRY_COOLDOWN
            logger.warning("Redis 缓存不可用，旁路（不影响检索正确性）：%s", e)
            return None

    def set(self, key: str, value, ttl: int) -> None:
        if not self._enabled() or time.time() < self._retry_at:
            return
        try:
            self._client().set(key, value, ex=ttl)
        except Exception as e:
            self._retry_at = time.time() + _RETRY_COOLDOWN
            logger.warning("Redis 缓存写入失败，旁路：%s", e)


_cache = _RedisCache()


def _digest(*parts: str) -> str:
    return md5("||".join(parts).encode("utf-8")).hexdigest()


# ---- 查询改写（隔离无关）----

def get_rewrite(question: str) -> list[str] | None:
    raw = _cache.get(f"cache:rw:{_digest(question)}")
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def set_rewrite(question: str, variants: list[str]) -> None:
    _cache.set(
        f"cache:rw:{_digest(question)}",
        json.dumps(variants, ensure_ascii=False),
        settings.cache_ttl_rewrite,
    )


# ---- query embedding（隔离无关）----

def get_embedding(text: str) -> list[float] | None:
    raw = _cache.get(f"cache:emb:{_digest(text)}")
    if raw is None:
        return None
    try:
        return list(struct.unpack(f"<{len(raw) // 4}f", raw))
    except Exception:
        return None


def set_embedding(text: str, vec: list[float]) -> None:
    # bge-m3 是 1024 维：存 JSON 约 20KB/条，打包成 float32 二进制降到 ~4KB
    _cache.set(
        f"cache:emb:{_digest(text)}",
        struct.pack(f"<{len(vec)}f", *vec),
        settings.cache_ttl_embedding,
    )


# ---- 最终检索结果（隔离敏感：key 必须带作用域）----

def _retrieval_key(
    query: str,
    allowed_doc_ids: list[str] | None,
    use_rerank: bool,
    candidate_pool: int,
    per_query_top: int,
    max_docs: int,
) -> str:
    scope = "*" if allowed_doc_ids is None else ",".join(sorted(allowed_doc_ids))
    return "cache:ret:" + _digest(
        query, scope, str(use_rerank), str(candidate_pool), str(per_query_top), str(max_docs)
    )


def get_retrieval(
    query: str,
    allowed_doc_ids: list[str] | None,
    use_rerank: bool,
    candidate_pool: int,
    per_query_top: int,
    max_docs: int,
) -> list[Document] | None:
    raw = _cache.get(
        _retrieval_key(query, allowed_doc_ids, use_rerank, candidate_pool, per_query_top, max_docs)
    )
    if raw is None:
        return None
    try:
        return [
            Document(page_content=it["c"], metadata=it["m"])
            for it in json.loads(raw)
        ]
    except Exception:
        return None


def set_retrieval(
    query: str,
    allowed_doc_ids: list[str] | None,
    docs: list[Document],
    use_rerank: bool,
    candidate_pool: int,
    per_query_top: int,
    max_docs: int,
) -> None:
    payload = json.dumps(
        [{"c": d.page_content, "m": d.metadata} for d in docs], ensure_ascii=False
    )
    _cache.set(
        _retrieval_key(query, allowed_doc_ids, use_rerank, candidate_pool, per_query_top, max_docs),
        payload,
        settings.cache_ttl_retrieval,
    )
