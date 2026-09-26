import uuid

import pytest

from app.config import get_settings
from app.db import get_session_factory
from app.errors import AppError
from app.providers.yandex_gpt import YandexGPTPro51Provider
from app.services.scan_service import process_scan_job
from app.worker.queue import dequeue_scan_job
from tests.conftest import unique_email

pytestmark = pytest.mark.asyncio


async def _seed_one_news_item(authed) -> str:
    email = unique_email("llm")
    await authed.post("/api/v1/auth/register", json={"name": "L", "email": email, "password": "correct-horse-battery"})
    flow = await authed.post(
        "/api/v1/flows", json={"name": "F", "theme": "Т", "domains": ["garant.ru"], "news_limit_per_run": 1}
    )
    flow_id = flow.json()["id"]
    await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "seed"})
    job_id_str = await dequeue_scan_job(timeout_seconds=1)
    factory = get_session_factory()
    async with factory() as db:
        await process_scan_job(db, uuid.UUID(job_id_str))
    news_list = (await authed.get("/api/v1/news")).json()
    return news_list[0]["id"]


async def test_llm_not_configured_without_key_and_fixture_disabled():
    settings = get_settings()
    original_fixture = settings.llm_fixture_mode
    settings.llm_fixture_mode = False
    try:
        provider = YandexGPTPro51Provider(settings=settings)
        factory = get_session_factory()
        async with factory() as db:
            with pytest.raises(AppError) as exc_info:
                await provider.check_relevance(
                    db, workspace_id=uuid.uuid4(), news_item_id=uuid.uuid4(), title="T", theme="Th"
                )
        assert exc_info.value.code == "LLM_NOT_CONFIGURED"
    finally:
        settings.llm_fixture_mode = original_fixture


async def test_llm_never_silently_swaps_model():
    """LLM_PROVIDER pinned to anything other than yandexgpt_pro_5_1 must refuse, even
    if fixture mode is on — ТЗ §10 forbids DeepSeek/Qwen/'latest'/etc substitution."""
    settings = get_settings()
    original_provider = settings.llm_provider
    settings.llm_provider = "deepseek"
    try:
        provider = YandexGPTPro51Provider(settings=settings)
        factory = get_session_factory()
        async with factory() as db:
            with pytest.raises(AppError) as exc_info:
                await provider.check_relevance(
                    db, workspace_id=uuid.uuid4(), news_item_id=uuid.uuid4(), title="T", theme="Th"
                )
        assert exc_info.value.code == "LLM_NOT_CONFIGURED"
    finally:
        settings.llm_provider = original_provider


async def test_fixture_mode_produces_labeled_draft_requiring_human_review(authed):
    news_id = await _seed_one_news_item(authed)
    resp = await authed.post(f"/api/v1/news/{news_id}/ai/draft", json={"expected_version": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "DEMO_FIXTURE" in body["news"]["text"]
    assert body["news"]["official_reviewed"] is False
    assert body["news"]["facts_reviewed"] is False
    assert body["news"]["status"] in ("NEEDS_REVIEW", "DRAFT")

    # Approval must still be impossible without a human review, even after AI ran.
    approve = await authed.post(f"/api/v1/news/{news_id}/approve")
    assert approve.status_code == 409


async def test_llm_call_is_logged_with_model_uri_and_no_api_key(authed):
    from sqlalchemy import select
    from app.models import LlmCall

    news_id = await _seed_one_news_item(authed)
    await authed.post(f"/api/v1/news/{news_id}/ai/draft", json={"expected_version": 1})

    factory = get_session_factory()
    async with factory() as db:
        calls = (await db.scalars(select(LlmCall).where(LlmCall.news_item_id == uuid.UUID(news_id)))).all()
        assert len(calls) >= 2  # relevance + fact_passport + draft
        for call in calls:
            assert call.model_uri.startswith("FIXTURE:")
            api_key = get_settings().yandex_api_key
            if api_key:
                assert api_key not in call.model_uri
            assert call.error == "" or "invalid_json" not in call.error


async def test_daily_token_budget_is_enforced(authed):
    settings = get_settings()
    original_budget = settings.llm_token_budget_per_workspace_day
    settings.llm_token_budget_per_workspace_day = 1  # anything at all trips it after one call
    try:
        email = unique_email("budget")
        resp = await authed.post(
            "/api/v1/auth/register", json={"name": "B", "email": email, "password": "correct-horse-battery"}
        )
        workspace_id = uuid.UUID(resp.json()["memberships"][0]["workspace_id"])

        provider = YandexGPTPro51Provider(settings=settings)
        factory = get_session_factory()
        async with factory() as db:
            await provider.check_relevance(db, workspace_id=workspace_id, news_item_id=None, title="T", theme="Th")
        async with factory() as db:
            with pytest.raises(AppError) as exc_info:
                await provider.check_relevance(db, workspace_id=workspace_id, news_item_id=None, title="T2", theme="Th2")
        assert exc_info.value.code == "LLM_BUDGET_EXCEEDED"
    finally:
        settings.llm_token_budget_per_workspace_day = original_budget
