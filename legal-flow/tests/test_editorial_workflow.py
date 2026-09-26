import uuid

import pytest

from app.db import get_session_factory
from app.services.scan_service import process_scan_job
from app.worker.queue import dequeue_scan_job
from tests.conftest import unique_email

pytestmark = pytest.mark.asyncio


async def _seed_one_news_item(authed) -> str:
    email = unique_email("editorial")
    await authed.post("/api/v1/auth/register", json={"name": "E", "email": email, "password": "correct-horse-battery"})
    flow = await authed.post(
        "/api/v1/flows",
        json={"name": "F", "theme": "Т", "domains": ["garant.ru"], "news_limit_per_run": 1},
    )
    flow_id = flow.json()["id"]
    await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "seed"})

    job_id_str = await dequeue_scan_job(timeout_seconds=1)
    factory = get_session_factory()
    async with factory() as db:
        await process_scan_job(db, uuid.UUID(job_id_str))

    news_list = (await authed.get("/api/v1/news")).json()
    return news_list[0]["id"]


async def test_cannot_approve_without_official_document_and_review(authed):
    news_id = await _seed_one_news_item(authed)

    approve = await authed.post(f"/api/v1/news/{news_id}/approve")
    assert approve.status_code == 409
    assert approve.json()["error"]["code"] == "OFFICIAL_SOURCE_MISSING"


async def test_editing_draft_resets_review_flags_and_bumps_version(authed):
    news_id = await _seed_one_news_item(authed)

    draft = await authed.patch(
        f"/api/v1/news/{news_id}/draft",
        json={"expected_version": 1, "title": "Новый заголовок", "text": "Самостоятельный текст материала."},
    )
    assert draft.status_code == 200, draft.text
    body = draft.json()
    assert body["version"] == 2
    assert body["official_reviewed"] is False
    assert body["facts_reviewed"] is False


async def test_stale_version_is_rejected(authed):
    news_id = await _seed_one_news_item(authed)

    await authed.patch(
        f"/api/v1/news/{news_id}/draft",
        json={"expected_version": 1, "title": "T1", "text": "Текст версии 2."},
    )
    stale = await authed.patch(
        f"/api/v1/news/{news_id}/draft",
        json={"expected_version": 1, "title": "T-stale", "text": "Конфликтующая правка."},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"


async def test_full_approve_publish_flow(authed):
    news_id = await _seed_one_news_item(authed)

    await authed.patch(
        f"/api/v1/news/{news_id}/draft",
        json={"expected_version": 1, "title": "Заголовок", "text": "Самостоятельный юридический текст."},
    )
    doc = await authed.put(
        f"/api/v1/news/{news_id}/official-document",
        json={
            "expected_version": 2,
            "title": "Определение суда",
            "url": "https://kad.arbitr.ru/Document/Pdf/example",
            "requisites": "Дело № А40-1/2026",
        },
    )
    assert doc.status_code == 200, doc.text
    assert doc.json()["version"] == 3

    facts = await authed.put(
        f"/api/v1/news/{news_id}/facts",
        json={"expected_version": 3, "text": "Проверенные факты дела.", "overall_status": "confirmed"},
    )
    assert facts.status_code == 200
    assert facts.json()["version"] == 4

    review = await authed.post(
        f"/api/v1/news/{news_id}/review",
        json={"official_reviewed": True, "facts_reviewed": True, "note": "Проверено лично"},
    )
    assert review.status_code == 200
    assert review.json()["status"] == "READY_FOR_REVIEW"

    approve = await authed.post(f"/api/v1/news/{news_id}/approve")
    assert approve.status_code == 200
    assert approve.json()["status"] == "APPROVED"

    publish = await authed.post(f"/api/v1/news/{news_id}/publish")
    assert publish.status_code == 200
    assert publish.json()["status"] == "PUBLISHED"

    preview = await authed.get(f"/api/v1/news/{news_id}/public-preview")
    assert preview.status_code == 200
    preview_body = preview.json()
    assert "discovery_domain" not in preview_body
    assert preview_body["official_document"]["url"] == "https://kad.arbitr.ru/Document/Pdf/example"


async def test_editing_official_document_resets_review_and_blocks_approve(authed):
    news_id = await _seed_one_news_item(authed)

    await authed.patch(f"/api/v1/news/{news_id}/draft", json={"expected_version": 1, "title": "T", "text": "Текст."})
    await authed.put(
        f"/api/v1/news/{news_id}/official-document",
        json={"expected_version": 2, "title": "Doc", "url": "https://example.com/doc", "requisites": "r"},
    )
    await authed.put(
        f"/api/v1/news/{news_id}/facts", json={"expected_version": 3, "text": "Факты", "overall_status": "confirmed"}
    )
    await authed.post(f"/api/v1/news/{news_id}/review", json={"official_reviewed": True, "facts_reviewed": True})

    # Editing the official document again must clear the review marks (ТЗ §9).
    changed = await authed.put(
        f"/api/v1/news/{news_id}/official-document",
        json={"expected_version": 4, "title": "Doc v2", "url": "https://example.com/doc-v2", "requisites": "r2"},
    )
    assert changed.json()["official_reviewed"] is False

    approve = await authed.post(f"/api/v1/news/{news_id}/approve")
    assert approve.status_code == 409
    assert approve.json()["error"]["code"] == "EDITOR_REVIEW_REQUIRED"


async def test_reject_requires_reason(authed):
    news_id = await _seed_one_news_item(authed)
    resp = await authed.post(f"/api/v1/news/{news_id}/reject", json={"reason": "Недостаточно подтверждённых фактов"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "REJECTED"
