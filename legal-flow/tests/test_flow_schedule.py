import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from app.db import get_session_factory
from app.models import NewsFlow, ScanJob
from app.models.enums import ScanJobStatus
from app.services.scan_service import MOSCOW_TZ, _is_schedule_due, enqueue_due_scheduled_scans
from tests.conftest import unique_email

# Same shared-database caveat as tests/test_worker_polling.py — enqueue_due_scheduled_scans
# scans every flow, not just this test's own, so a leftover "due" flow from one test would
# spuriously fire in a later test. Each test sets schedule_period back to "Вручную" (never
# auto-due) before returning.


async def _create_flow(authed, *, schedule_period: str = "Вручную", schedule_time: str = "09:00") -> uuid.UUID:
    email = unique_email("flow-schedule")
    await authed.post("/api/v1/auth/register", json={"name": "S", "email": email, "password": "correct-horse-battery"})
    flow = await authed.post(
        "/api/v1/flows",
        json={
            "name": "F", "theme": "Т", "domains": ["garant.ru"], "news_limit_per_run": 1,
            "schedule_period": schedule_period, "schedule_time": schedule_time,
        },
    )
    return uuid.UUID(flow.json()["id"])


def test_manual_period_is_never_due():
    now = datetime(2026, 9, 29, 12, 0, tzinfo=MOSCOW_TZ)
    assert _is_schedule_due("Вручную", "09:00", None, now) is False


def test_daily_due_before_time_of_day_is_false():
    now = datetime(2026, 9, 29, 8, 0, tzinfo=MOSCOW_TZ)
    assert _is_schedule_due("Ежедневно", "09:00", None, now) is False


def test_daily_due_first_run_after_time_of_day():
    now = datetime(2026, 9, 29, 9, 30, tzinfo=MOSCOW_TZ)
    assert _is_schedule_due("Ежедневно", "09:00", None, now) is True


def test_daily_not_due_again_same_day():
    now = datetime(2026, 9, 29, 18, 0, tzinfo=MOSCOW_TZ)
    last_scan = datetime(2026, 9, 29, 9, 5, tzinfo=MOSCOW_TZ)
    assert _is_schedule_due("Ежедневно", "09:00", last_scan, now) is False


def test_daily_due_again_next_day():
    now = datetime(2026, 9, 30, 9, 30, tzinfo=MOSCOW_TZ)
    last_scan = datetime(2026, 9, 29, 9, 5, tzinfo=MOSCOW_TZ)
    assert _is_schedule_due("Ежедневно", "09:00", last_scan, now) is True


def test_weekday_only_skips_saturday_and_sunday():
    saturday = datetime(2026, 10, 3, 10, 0, tzinfo=MOSCOW_TZ)  # 2026-10-03 is a Saturday
    assert saturday.weekday() == 5
    assert _is_schedule_due("По рабочим дням", "09:00", None, saturday) is False
    monday = datetime(2026, 10, 5, 10, 0, tzinfo=MOSCOW_TZ)
    assert _is_schedule_due("По рабочим дням", "09:00", None, monday) is True


def test_weekly_waits_seven_days():
    now = datetime(2026, 10, 3, 10, 0, tzinfo=MOSCOW_TZ)
    last_scan_five_days_ago = datetime(2026, 9, 28, 9, 0, tzinfo=MOSCOW_TZ)
    assert _is_schedule_due("Раз в неделю", "09:00", last_scan_five_days_ago, now) is False
    last_scan_eight_days_ago = datetime(2026, 9, 25, 9, 0, tzinfo=MOSCOW_TZ)
    assert _is_schedule_due("Раз в неделю", "09:00", last_scan_eight_days_ago, now) is True


@pytest.mark.asyncio
async def test_enqueue_creates_job_for_due_flow_and_sets_last_scan_at(authed):
    flow_id = await _create_flow(authed, schedule_period="Ежедневно", schedule_time="00:00")
    factory = get_session_factory()
    try:
        async with factory() as db:
            created_job_ids = await enqueue_due_scheduled_scans(db)
            assert len(created_job_ids) == 1

            flow = await db.get(NewsFlow, flow_id)
            assert flow.last_scan_at is not None

            jobs = (await db.scalars(select(ScanJob).where(ScanJob.flow_id == flow_id))).all()
            assert [j.id for j in jobs] == created_job_ids

            # Calling again right away must not create a second job for today.
            created_again = await enqueue_due_scheduled_scans(db)
            assert created_again == []
            jobs_after = (await db.scalars(select(ScanJob).where(ScanJob.flow_id == flow_id))).all()
            assert len(jobs_after) == 1
    finally:
        # Leaves no pending scan_job behind — tests/test_worker_polling.py and the
        # run_next_pending_job() helper poll the whole table, not just their own flow,
        # so a leftover pending row here would steal a later test's job.
        async with factory() as db:
            flow = await db.get(NewsFlow, flow_id)
            flow.schedule_period = "Вручную"
            for job in (await db.scalars(select(ScanJob).where(ScanJob.flow_id == flow_id))).all():
                job.status = ScanJobStatus.done
            await db.commit()


@pytest.mark.asyncio
async def test_enqueue_skips_manual_flows(authed):
    flow_id = await _create_flow(authed, schedule_period="Вручную")
    factory = get_session_factory()
    async with factory() as db:
        created_job_ids = await enqueue_due_scheduled_scans(db)
        jobs = (await db.scalars(select(ScanJob).where(ScanJob.flow_id == flow_id))).all()
        assert jobs == []
        assert created_job_ids == []
