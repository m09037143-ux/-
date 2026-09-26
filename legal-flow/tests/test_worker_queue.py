import asyncio

import pytest

from app.worker.queue import dequeue_scan_job, enqueue_scan_job

pytestmark = pytest.mark.asyncio


async def test_enqueue_then_dequeue_round_trip():
    await enqueue_scan_job("job-abc")
    result = await dequeue_scan_job(timeout_seconds=2)
    assert result == "job-abc"


async def test_dequeue_returns_none_on_empty_queue_without_raising():
    result = await dequeue_scan_job(timeout_seconds=1)
    assert result is None


async def test_dequeue_sees_a_push_that_lands_mid_wait():
    """Regression test for a real bug found while live-testing the worker: a job
    pushed to Redis a couple of seconds after the worker's poll loop had already
    started waiting must still be picked up before the wait window ends, not lost
    or blown up by a client-side timeout."""

    async def push_soon():
        await asyncio.sleep(1)
        await enqueue_scan_job("job-delayed")

    push_task = asyncio.create_task(push_soon())
    result = await dequeue_scan_job(timeout_seconds=3)
    await push_task
    assert result == "job-delayed"
