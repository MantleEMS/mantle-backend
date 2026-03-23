import redis.asyncio as aioredis
from app.config import settings

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis


async def close_redis():
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


# Convenience helpers

async def redis_publish(channel: str, message: str):
    r = await get_redis()
    await r.publish(channel, message)


async def redis_incr(key: str) -> int:
    r = await get_redis()
    return await r.incr(key)


async def redis_zadd(key: str, mapping: dict):
    r = await get_redis()
    await r.zadd(key, mapping)


async def redis_zrangebyscore(key: str, min_score: float, max_score: float):
    r = await get_redis()
    return await r.zrangebyscore(key, min_score, max_score)


async def redis_zrem(key: str, *members):
    r = await get_redis()
    await r.zrem(key, *members)


async def redis_setex(key: str, seconds: int, value: str):
    r = await get_redis()
    await r.setex(key, seconds, value)
