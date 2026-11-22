"""按租户的日请求配额。Redis 全局计数；Redis 不可用时退回进程内（单机有效）。

限流（每分钟/按 IP）和配额（每天/按租户）是两层：限流防突发，配额防一个租户长期打爆。
"""
import logging
import threading
import time
from datetime import datetime, timezone

from fastapi import Depends, HTTPException

from app.config import settings
from app.core.security import UserContext, get_current_user

logger = logging.getLogger("app.quota")

_lock = threading.Lock()
_counts: dict[tuple[str, str], int] = {}
_redis_retry_at = 0.0


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _key(tenant_id: str) -> str:
    return f"quota:{tenant_id}:{_today()}"


def reset_quota() -> None:
    with _lock:
        _counts.clear()


def check_and_increment(tenant_id: str) -> tuple[bool, int]:
    limit = settings.daily_request_quota
    if limit <= 0:
        return True, 0

    global _redis_retry_at
    key = _key(tenant_id)
    if settings.redis_url and time.time() >= _redis_retry_at:
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url, socket_connect_timeout=1, socket_timeout=1
            )
            count = client.incr(key)
            if count == 1:
                client.expire(key, 86400)
            return count <= limit, count
        except Exception as exc:
            _redis_retry_at = time.time() + 30.0
            logger.warning("Redis 配额不可用，退回进程内计数: %s", exc)

    with _lock:
        count = _counts.get((tenant_id, _today()), 0) + 1
        _counts[(tenant_id, _today())] = count
    return count <= limit, count


def enforce_quota(user: UserContext = Depends(get_current_user)) -> UserContext:
    allowed, count = check_and_increment(user.tenant_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"租户日请求配额已用完（{settings.daily_request_quota}/天）",
        )
    return user
