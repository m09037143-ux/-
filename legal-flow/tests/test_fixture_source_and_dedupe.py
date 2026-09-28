import pytest

from tests.conftest import run_next_pending_job, unique_email

pytestmark = pytest.mark.asyncio


async def _register_and_get_csrf(authed, email_prefix: str):
    email = unique_email(email_prefix)
    await authed.post("/api/v1/auth/register", json={"name": "N", "email": email, "password": "correct-horse-battery"})


async def test_scan_job_creates_fixture_news_labeled_demo(authed):
    await _register_and_get_csrf(authed, "scan")
    flow = await authed.post(
        "/api/v1/flows",
        json={
            "name": "Банкротство",
            "theme": "Банкротство и корпоративные споры",
            "domains": ["garant.ru", "pravo.ru"],
            "news_limit_per_run": 3,
        },
    )
    assert flow.status_code == 201, flow.text
    flow_id = flow.json()["id"]

    job_resp = await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "job-1"})
    assert job_resp.status_code == 202
    assert job_resp.json()["status"] == "pending"

    job = await run_next_pending_job()
    assert job.status.value == "done"
    # Fixture set has 3 canned headlines total (2 on garant.ru, 1 on pravo.ru); with
    # both domains allowed and limit=3, all 3 match.
    assert len(job.created_news_ids) == 3

    news_list = await authed.get("/api/v1/news")
    assert news_list.status_code == 200
    titles = [n["title"] for n in news_list.json()]
    assert any("Ответственность руководителя" in t for t in titles)
    for n in news_list.json():
        assert n["status"] == "DISCOVERED"


async def test_scan_job_idempotency_key_does_not_duplicate(authed):
    await _register_and_get_csrf(authed, "scan-idem")
    flow = await authed.post(
        "/api/v1/flows",
        json={"name": "F", "theme": "Т", "domains": ["garant.ru"], "news_limit_per_run": 3},
    )
    flow_id = flow.json()["id"]

    r1 = await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "same-key"})
    job_id_1 = r1.json()["id"]
    await run_next_pending_job()

    # Same idempotency key again: must return the SAME job (now already "done"),
    # not create a new pending row, and not create new items.
    r2 = await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "same-key"})
    assert r2.json()["id"] == job_id_1

    from app.db import get_session_factory
    from app.worker.runner import fetch_next_pending_job_id

    factory = get_session_factory()
    async with factory() as db:
        assert await fetch_next_pending_job_id(db) is None

    news_list = (await authed.get("/api/v1/news")).json()
    # Fixture set has 2 garant.ru-only headlines out of 3 total.
    assert len(news_list) == 2


async def test_repeated_runs_dedupe_same_story(authed):
    await _register_and_get_csrf(authed, "scan-dedupe")
    flow = await authed.post(
        "/api/v1/flows",
        json={"name": "F2", "theme": "Т2", "domains": ["garant.ru"], "news_limit_per_run": 3},
    )
    flow_id = flow.json()["id"]

    await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "run-1"})
    job1 = await run_next_pending_job()
    assert len(job1.created_news_ids) == 2

    # A second, distinct job (different idempotency key) rediscovering the SAME
    # fixture story must not create a second NewsItem (ТЗ §9 antidup by story_key).
    await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "run-2"})
    job2 = await run_next_pending_job()
    assert len(job2.created_news_ids) == 0

    news_list = (await authed.get("/api/v1/news")).json()
    assert len(news_list) == 2

    # The API surfaces *why* the second run found nothing new, so the frontend
    # can tell "nothing found" apart from "found, but already collected".
    job2_api = (await authed.get(f"/api/v1/scan-jobs/{job2.id}")).json()
    assert job2_api["duplicate_count"] == 2


async def test_domain_without_source_policy_is_blocked_not_collected(authed):
    await _register_and_get_csrf(authed, "scan-policy")
    flow = await authed.post(
        "/api/v1/flows",
        json={"name": "F3", "theme": "Т3", "domains": ["unknown-legal-site.ru"], "news_limit_per_run": 3},
    )
    flow_id = flow.json()["id"]

    await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "policy-1"})
    job = await run_next_pending_job()
    assert job.status.value == "done"
    assert job.created_news_ids == []

    activity = await authed.get("/api/v1/activity")
    actions = [e["action"] for e in activity.json()]
    assert "source_policy_review_required" in actions
