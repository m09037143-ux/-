import asyncio

from app.redis_client import get_redis

QUEUE_KEY = "scan_jobs:queue"
_POLL_INTERVAL_SECONDS = 0.5


async def enqueue_scan_job(job_id: str) -> None:
    await get_redis().rpush(QUEUE_KEY, job_id)


async def dequeue_scan_job(timeout_seconds: float = 5) -> str | None:
    """Short-poll LPOP instead of BLPOP: BLPOP ties up the connection's socket-level
    read for the full wait and, in this environment, occasionally raises a client-side
    TimeoutError right as the server's own block expires instead of returning the
    normal nil reply — which would silently kill the worker loop. Polling avoids that
    whole class of race entirely and costs nothing at this queue's volume."""
    elapsed = 0.0
    while elapsed < timeout_seconds:
        job_id = await get_redis().lpop(QUEUE_KEY)
        if job_id is not None:
            return job_id
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        elapsed += _POLL_INTERVAL_SECONDS
    return None
