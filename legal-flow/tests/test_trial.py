import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import get_session_factory
from app.models import Trial
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
