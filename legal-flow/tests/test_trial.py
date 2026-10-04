import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import get_session_factory
from app.models import Trial, User
from tests.conftest import unique_email

pytestmark = pytest.mark.asyncio


async def test_trial_expires_after_72_hours_and_blocks_mutations(authed):
    email = unique_email("trial")
    resp = await authed.post(
        "/api/v1/auth/register", json={"name": "T", "email": email, "password": "correct-horse-battery"}
    )
    workspace_id = uuid.UUID(resp.json()["memberships"][0]["workspace_id"])

    access = (await authed.get("/api/v1/access")).json()
    assert access["trial_state"] == "active"
    started = datetime.fromisoformat(access["trial_started_at"])
    ends = datetime.fromisoformat(access["trial_ends_at"])
    assert ends - started == timedelta(hours=72)

    # Simulate 72h+ elapsing by moving the server-authoritative row into the past —
    # nothing client-side (localStorage clearing, re-login) can do this in the real API.
    factory = get_session_factory()
    async with factory() as session:
        trial = await session.scalar(select(Trial).where(Trial.workspace_id == workspace_id))
        trial.started_at = datetime.now(timezone.utc) - timedelta(hours=100)
        trial.ends_at = trial.started_at + timedelta(hours=72)
        await session.commit()

    access2 = (await authed.get("/api/v1/access")).json()
    assert access2["trial_state"] == "expired"
    assert access2["can_mutate"] is False


async def test_platform_admin_bypasses_expired_trial_and_gets_top_plan(authed):
    email = unique_email("trial-admin")
    resp = await authed.post(
        "/api/v1/auth/register", json={"name": "A", "email": email, "password": "correct-horse-battery"}
    )
    workspace_id = uuid.UUID(resp.json()["memberships"][0]["workspace_id"])
    user_id = uuid.UUID(resp.json()["id"])

    factory = get_session_factory()
    async with factory() as session:
        trial = await session.scalar(select(Trial).where(Trial.workspace_id == workspace_id))
        trial.started_at = datetime.now(timezone.utc) - timedelta(hours=100)
        trial.ends_at = trial.started_at + timedelta(hours=72)
        await session.commit()

    expired = (await authed.get("/api/v1/access")).json()
    assert expired["trial_state"] == "expired"
    assert expired["can_mutate"] is False

    async with factory() as session:
        user = await session.get(User, user_id)
        user.is_platform_admin = True
        await session.commit()

    admin_access = (await authed.get("/api/v1/access")).json()
    assert admin_access["trial_state"] == "expired"  # Trial.ends_at itself is never touched
    assert admin_access["can_mutate"] is True
    assert admin_access["plan_code"] == "editorial"


async def test_relogin_does_not_reset_trial_clock(authed):
    email = unique_email("trial-relogin")
    resp = await authed.post(
        "/api/v1/auth/register", json={"name": "T2", "email": email, "password": "correct-horse-battery"}
    )
    first_ends_at = (await authed.get("/api/v1/access")).json()["trial_ends_at"]

    await authed.post("/api/v1/auth/logout")
    await authed.post("/api/v1/auth/login", json={"email": email, "password": "correct-horse-battery"})

    second_ends_at = (await authed.get("/api/v1/access")).json()["trial_ends_at"]
    assert first_ends_at == second_ends_at
