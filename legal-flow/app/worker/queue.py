from app.redis_client import get_redis

QUEUE_KEY = "scan_jobs:queue"


async def enqueue_scan_job(job_id: str) -> None:
    await get_redis().rpush(QUEUE_KEY, job_id)


async def dequeue_scan_job(timeout_seconds: int = 5) -> str | None:
    result = await get_redis().blpop(QUEUE_KEY, timeout=timeout_seconds)
    if result is None:
        return None
    _key, job_id = result
    return job_id
