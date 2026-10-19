import logging
import time

from app.config import settings

logger = logging.getLogger("app.limiter")

_RETRY_COOLDOWN = 30.0  # Redis 挂了之后隔多久再探一次，避免每请求都等连接超时


class RateLimiter:
    """跨进程限流：Redis 全局计数优先，失败退回本进程内存。

    为什么不能只用进程内计数：`--scale app=N` 后每个副本各算一份，真实放行量 = limit × N，
    限流形同虚设。Redis 的 INCR 是全局原子的，所有副本累加同一个 key。

    窗口按固定时长切 bucket（key 带 bucket 号），INCR + EXPIRE 一步到位；代价是窗口边界
    处理论可翻倍突发（tumbling window 的固有属性），演示量级够用，要精确可换滑动窗口 zset。

    Redis 不可用时退回进程内实现——宁可弱限流（只本进程有效）也不给用户 500，
    并在日志里点明"多副本下限流会被放大"。隔 _RETRY_COOLDOWN 秒再重试 Redis。
    """

    def __init__(self) -> None:
        self._local: dict[str, list] = {}
        self._redis = None
        self._retry_at = 0.0

    async def _client(self):
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        return self._redis

    async def over_limit(self, client_key: str) -> bool:
        limit = settings.rate_limit_per_min
        if limit <= 0:
            return False
        window = max(1, settings.rate_limit_window_seconds)
        if settings.redis_url and time.time() >= self._retry_at:
            try:
                return await self._redis_over_limit(client_key, limit, window)
            except Exception as e:
                self._retry_at = time.time() + _RETRY_COOLDOWN
                logger.warning(
                    "Redis 限流不可用，本进程退回本地计数（多副本下限流会被放大）：%s", e
                )
        return self._local_over_limit(client_key, limit, window)

    async def _redis_over_limit(self, client_key: str, limit: int, window: int) -> bool:
        r = await self._client()
        key = f"rl:{client_key}:{int(time.time() // window)}"
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, window + 1)
        count, _ = await pipe.execute()
        return int(count) > limit

    def _local_over_limit(self, client_key: str, limit: int, window: int) -> bool:
        now = time.time()
        entry = self._local.get(client_key)
        if entry is None or now - entry[0] >= window:
            self._local[client_key] = [now, 1]
            if len(self._local) > 10_000:  # 防无界增长，顺手清一波
                self._local.clear()
            return False
        if entry[1] >= limit:
            return True
        entry[1] += 1
        return False


limiter = RateLimiter()
