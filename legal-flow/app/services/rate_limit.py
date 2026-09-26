from app.errors import AppError
from app.redis_client import get_redis


async def enforce_rate_limit(bucket: str, key: str, limit: int, window_seconds: int) -> None:
    """Fixed-window counter in Redis. Fails open only if Redis itself is unreachable
    would be wrong for auth endpoints, so a Redis error propagates as a 5xx rather
    than silently disabling the limit."""
    redis = get_redis()
    redis_key = f"ratelimit:{bucket}:{key}"
    count = await redis.incr(redis_key)
    if count == 1:
        await redis.expire(redis_key, window_seconds)
    if count > limit:
        raise AppError("RATE_LIMITED", f"Слишком много запросов ({bucket}). Попробуйте позже.")
