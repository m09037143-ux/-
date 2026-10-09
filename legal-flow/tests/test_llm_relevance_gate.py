import uuid

import pytest
from sqlalchemy import select

from app.db import get_session_factory
from app.errors import AppError
from app.models import Discovery, NewsItem
from app.providers.yandex_gpt import RelevanceResult
from app.services import ai_service, scan_service
from tests.conftest import run_next_pending_job, unique_email

# FixtureSourceProvider's 3 canned headlines (see app/providers/fixture_source.py) —
# used here as deterministic candidates to gate on, not to test the fixture provider
# itself.
_REJECTED_TITLE = "Взыскание убытков с бывшего директора: обстоятельства спора"


async def _create_flow(authed, prefix: str) -> uuid.UUID:
    email = unique_email(prefix)
    await authed.post("/api/v1/auth/register", json={"name": "R", "email": email, "password": "correct-horse-battery"})
    flow = await authed.post(
        "/api/v1/flows",
        json={"name": "F", "theme": "Т", "domains": ["garant.ru", "pravo.ru"], "news_limit_per_run": 3},
    )
    return uuid.UUID(flow.json()["id"])


@pytest.mark.asyncio
async def test_llm_marks_one_candidate_irrelevant_and_it_gets_no_news_item(authed, monkeypatch):
    async def fake_check_relevance(db, *, workspace_id, news_item_id, title, theme):
        assert news_item_id is None  # no NewsItem exists yet at this point
        is_relevant = title != _REJECTED_TITLE
        return RelevanceResult(is_relevant=is_relevant, reasoning="test")

    monkeypatch.setattr(scan_service._llm_provider, "check_relevance", fake_check_relevance)

    flow_id = await _create_flow(authed, "relevance-reject")
    await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "k1"})
    job = await run_next_pending_job()

    assert job.status.value == "done"
    assert len(job.created_news_ids) == 2  # 3 fixture headlines minus the 1 rejected

    factory = get_session_factory()
    async with factory() as db:
        created_ids = [uuid.UUID(x) for x in job.created_news_ids]
        titles = {n.title for n in (await db.scalars(select(NewsItem).where(NewsItem.id.in_(created_ids)))).all()}
        assert _REJECTED_TITLE not in titles

        rejected_discovery = await db.scalar(
            select(Discovery).where(
                Discovery.scan_job_id == job.id, Discovery.decision_reason == "not_relevant_to_theme"
            )
        )
        assert rejected_discovery is not None


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_including_all_candidates(authed, monkeypatch):
    calls = []

    async def failing_check_relevance(db, *, workspace_id, news_item_id, title, theme):
        calls.append(title)
        raise AppError("LLM_NOT_CONFIGURED", "YandexGPT не настроен.")

    monkeypatch.setattr(scan_service._llm_provider, "check_relevance", failing_check_relevance)

    flow_id = await _create_flow(authed, "relevance-fallback")
    await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "k1"})
    job = await run_next_pending_job()

    assert job.status.value == "done"
    assert len(job.created_news_ids) == 3  # all 3 fixture headlines, none dropped
    assert len(calls) == 1  # stopped calling the LLM after the first failure


@pytest.mark.asyncio
async def test_draft_generation_survives_relevance_check_refusing_non_json(authed, monkeypatch):
    """Real YandexGPT occasionally answers a sensitive-looking headline with a plain-text
    refusal instead of the requested JSON — this used to hard-fail the whole draft (the
    relevance check here is informational only, logged but never gates anything in
    ai_service.generate_ai_draft, so a failure here must not block the rest)."""
    flow_id = await _create_flow(authed, "relevance-draft-resilience")
    await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "seed"})
    job = await run_next_pending_job()
    news_id = job.created_news_ids[0]

    async def refusing_check_relevance(db, *, workspace_id, news_item_id, title, theme):
        raise AppError("VALIDATION_ERROR", "Ответ модели не прошёл проверку структуры: invalid_json_or_schema")

    monkeypatch.setattr(ai_service._provider, "check_relevance", refusing_check_relevance)

    resp = await authed.post(f"/api/v1/news/{news_id}/ai/draft", json={"expected_version": 1})
    assert resp.status_code == 200, resp.text
    assert resp.json()["relevance"]["is_relevant"] is True
    assert "VALIDATION_ERROR" in resp.json()["relevance"]["reasoning"]
    assert resp.json()["news"]["text"]  # draft text was still generated


@pytest.mark.asyncio
async def test_draft_generation_passes_flow_theme_not_news_title_to_relevance_check(authed, monkeypatch):
    email = unique_email("relevance-theme")
    await authed.post("/api/v1/auth/register", json={"name": "R", "email": email, "password": "correct-horse-battery"})
    flow = await authed.post(
        "/api/v1/flows",
        json={"name": "F", "theme": "Особая тема потока", "domains": ["garant.ru"], "news_limit_per_run": 1},
    )
    flow_id = flow.json()["id"]
    await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "seed"})
    job = await run_next_pending_job()
    news_id = job.created_news_ids[0]

    seen_themes = []

    async def capturing_check_relevance(db, *, workspace_id, news_item_id, title, theme):
        seen_themes.append(theme)
        return RelevanceResult(is_relevant=True, reasoning="test")

    monkeypatch.setattr(ai_service._provider, "check_relevance", capturing_check_relevance)

    resp = await authed.post(f"/api/v1/news/{news_id}/ai/draft", json={"expected_version": 1})
    assert resp.status_code == 200, resp.text
    assert seen_themes == ["Особая тема потока"]
