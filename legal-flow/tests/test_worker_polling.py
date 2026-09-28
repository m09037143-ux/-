import uuid

import pytest

from app.db import get_session_factory
from app.models import ScanJob
from app.models.enums import ScanJobStatus
from app.worker.runner import fetch_next_pending_job_id
from tests.conftest import unique_email

pytestmark = pytest.mark.asyncio

# These tests share one Postgres database for the whole pytest session (see
# conftest._prepare_database), and fetch_next_pending_job_id polls across the
# entire scan_jobs table, not scoped to one flow — so every job left "pending"
# here would otherwise leak into and contaminate whichever test runs next.
# Each test cleans up its own rows before returning.


async def _create_flow(authed) -> uuid.UUID:
    email = unique_email("worker-poll")
    await authed.post("/api/v1/auth/register", json={"name": "W", "email": email, "password": "correct-horse-battery"})
    flow = await authed.post(
        "/api/v1/flows", json={"name": "F", "theme": "Т", "domains": ["garant.ru"], "news_limit_per_run": 1}
    )
    return uuid.UUID(flow.json()["id"])


async def test_returns_none_when_nothing_pending(authed):
    await _create_flow(authed)
    factory = get_session_factory()
    async with factory() as db:
        assert await fetch_next_pending_job_id(db) is None


async def test_finds_the_pending_job_and_ignores_others(authed):
    flow_id = await _create_flow(authed)
    factory = get_session_factory()
    async with factory() as db:
        done = ScanJob(flow_id=flow_id, idempotency_key="k1", status=ScanJobStatus.done, provider_name="fixture")
        running = ScanJob(flow_id=flow_id, idempotency_key="k2", status=ScanJobStatus.running, provider_name="fixture")
        pending = ScanJob(flow_id=flow_id, idempotency_key="k3", status=ScanJobStatus.pending, provider_name="fixture")
        db.add_all([done, running, pending])
        await db.commit()

        found = await fetch_next_pending_job_id(db)
        assert found == pending.id

        pending.status = ScanJobStatus.done
        await db.commit()


async def test_picks_the_oldest_pending_job_first(authed):
    """Two pending jobs — the worker must not skip ahead to a newer one while an
    older one is still waiting."""
    flow_id = await _create_flow(authed)
    factory = get_session_factory()
    async with factory() as db:
        older = ScanJob(flow_id=flow_id, idempotency_key="older", status=ScanJobStatus.pending, provider_name="fixture")
        db.add(older)
        await db.commit()

        newer = ScanJob(flow_id=flow_id, idempotency_key="newer", status=ScanJobStatus.pending, provider_name="fixture")
        db.add(newer)
        await db.commit()

        found = await fetch_next_pending_job_id(db)
        assert found == older.id

        older.status = ScanJobStatus.done
        newer.status = ScanJobStatus.done
        await db.commit()
