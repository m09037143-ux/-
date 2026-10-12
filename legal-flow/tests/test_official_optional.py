import uuid
from types import SimpleNamespace

import pytest

from app.errors import AppError
from app.models.enums import NewsStatus
from app.services import news_service


class FakeDb:
    def __init__(self, doc=None):
        self.doc = doc
        self.added, self.commits = [], 0

    async def scalar(self, *_a, **_k):
        return self.doc

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


async def _noop_log(db, **kw):
    return None


@pytest.fixture(autouse=True)
def _quiet_log(monkeypatch):
    monkeypatch.setattr(news_service, "log_activity", _noop_log)


def _news(**kw):
    base = dict(
        id=uuid.uuid4(), workspace_id=uuid.uuid4(), status=NewsStatus.NEEDS_REVIEW,
        official_reviewed=False, facts_reviewed=False, approved_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _doc(checked=False):
    return SimpleNamespace(title="Определение суда", url="https://kad.arbitr.ru/doc", checked_at=None if not checked else "x")


@pytest.mark.asyncio
async def test_review_without_document_needs_only_facts():
    news = _news()
    await news_service.submit_review(FakeDb(None), news=news, reviewer_id=uuid.uuid4(), official_reviewed=False, facts_reviewed=True, note="")
    assert news.status == NewsStatus.READY_FOR_REVIEW


@pytest.mark.asyncio
async def test_review_without_document_and_without_facts_is_not_ready():
    news = _news()
    await news_service.submit_review(FakeDb(None), news=news, reviewer_id=uuid.uuid4(), official_reviewed=False, facts_reviewed=False, note="")
    assert news.status == NewsStatus.NEEDS_REVIEW


@pytest.mark.asyncio
async def test_review_with_document_requires_checking_it_too():
    news, doc = _news(), _doc()
    await news_service.submit_review(FakeDb(doc), news=news, reviewer_id=uuid.uuid4(), official_reviewed=False, facts_reviewed=True, note="")
    assert news.status == NewsStatus.NEEDS_REVIEW and doc.checked_at is None
    await news_service.submit_review(FakeDb(doc), news=news, reviewer_id=uuid.uuid4(), official_reviewed=True, facts_reviewed=True, note="")
    assert news.status == NewsStatus.READY_FOR_REVIEW and doc.checked_at is not None


@pytest.mark.asyncio
async def test_approve_without_document_needs_facts_review_only():
    ready = _news(status=NewsStatus.READY_FOR_REVIEW, facts_reviewed=True)
    await news_service.approve(FakeDb(None), news=ready)
    assert ready.status == NewsStatus.APPROVED and ready.approved_at is not None


@pytest.mark.asyncio
async def test_approve_without_facts_review_is_refused_with_a_clear_message():
    news = _news(status=NewsStatus.READY_FOR_REVIEW, facts_reviewed=False)
    with pytest.raises(AppError) as exc:
        await news_service.approve(FakeDb(None), news=news)
    assert exc.value.code == "EDITOR_REVIEW_REQUIRED" and "фактов" in exc.value.message and "документа" not in exc.value.message


@pytest.mark.asyncio
async def test_approve_with_unchecked_document_is_refused():
    news = _news(status=NewsStatus.READY_FOR_REVIEW, facts_reviewed=True, official_reviewed=False)
    with pytest.raises(AppError) as exc:
        await news_service.approve(FakeDb(_doc()), news=news)
    assert exc.value.code == "EDITOR_REVIEW_REQUIRED" and "документа и фактов" in exc.value.message
    ok = _news(status=NewsStatus.READY_FOR_REVIEW, facts_reviewed=True, official_reviewed=True)
    await news_service.approve(FakeDb(_doc(checked=True)), news=ok)
    assert ok.status == NewsStatus.APPROVED


@pytest.mark.asyncio
async def test_approve_still_requires_ready_for_review_status():
    news = _news(status=NewsStatus.NEEDS_REVIEW, facts_reviewed=True)
    with pytest.raises(AppError) as exc:
        await news_service.approve(FakeDb(None), news=news)
    assert exc.value.code == "EDITOR_REVIEW_REQUIRED"
