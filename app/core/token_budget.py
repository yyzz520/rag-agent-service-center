"""租户日 token 预算：Redis INCRBY 全局计数，Redis 不可用退回进程内（单机有效）。

请求配额（quota）管次数，这里管消耗：一次长会话可能只 1 个请求却烧几万 token。
用法：请求前 `check()`（超预算直接拒绝），请求结束 `add(本次 usage.total_tokens)`。
预算 <= 0 表示关闭。
"""
import logging
import threading
import time
from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger("app.token_budget")

_lock = threading.Lock()
_used: dict[tuple[str, str], int] = {}
_redis_retry_at = 0.0


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _key(tenant_id: str) -> str:
    return f"tokens:{tenant_id}:{_today()}"


def reset() -> None:
    with _lock:
        _used.clear()


def _redis_get(key: str) -> int | None:
    global _redis_retry_at
    if not settings.redis_url or time.time() < _redis_retry_at:
        return None
    try:
        import redis

        client = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=1, socket_timeout=1
        )
        value = client.get(key)
        return int(value or 0)
    except Exception as exc:
        _redis_retry_at = time.time() + 30.0
        logger.warning("Redis token 预算不可用，退回进程内计数: %s", exc)
        return None


def check(tenant_id: str) -> tuple[bool, int]:
    """返回 (是否还有预算, 今日已用 token)；预算关闭时恒为 (True, 0)。"""
    limit = settings.tenant_daily_token_budget
    if limit <= 0:
        return True, 0
    key = _key(tenant_id)
    used = _redis_get(key)
    if used is None:
        with _lock:
            used = _used.get((tenant_id, _today()), 0)
    return used < limit, used


def add(tenant_id: str, tokens: int) -> int:
    """请求结束后记账；返回今日累计（预算关闭时直接返回 0）。"""
    global _redis_retry_at
    if tokens <= 0 or settings.tenant_daily_token_budget <= 0:
        return 0
    key = _key(tenant_id)
    if settings.redis_url and time.time() >= _redis_retry_at:
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url, socket_connect_timeout=1, socket_timeout=1
            )
            total = client.incrby(key, int(tokens))
            if total == int(tokens):
                client.expire(key, 86400)
            return int(total)
        except Exception as exc:
            _redis_retry_at = time.time() + 30.0
            logger.warning("Redis token 预算不可用，退回进程内计数: %s", exc)
    with _lock:
        day = _today()
        total = _used.get((tenant_id, day), 0) + int(tokens)
        _used[(tenant_id, day)] = total
        return total
