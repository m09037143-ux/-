from redis.asyncio import Redis, from_url

from app.config import get_settings

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def reset_redis_for_tests(url: str) -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
    _redis = from_url(url, decode_responses=True)
