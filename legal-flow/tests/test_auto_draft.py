import uuid
from types import SimpleNamespace

import pytest

from app.errors import AppError
from app.models import NewsFlow, NewsItem
from app.models.enums import NewsStatus
from app.services import ai_service

WORKSPACE = uuid.uuid4()
OWNER = uuid.uuid4()


class FakeDb:
    """Только то, что трогает auto_generate_drafts: get по модели и id, scalar (владелец), rollback."""

    def __init__(self, flow, news_items, owner=OWNER):
        self.flow = flow
        self.news = {n.id: n for n in news_items}
        self.owner = owner
        self.rollbacks = 0

    async def get(self, model, key):
        if model is NewsFlow:
            return self.flow
        if model is NewsItem:
            return self.news.get(key)
        return None

    async def scalar(self, *_a, **_k):
        return self.owner

    async def rollback(self):
        self.rollbacks += 1


def _news(status=NewsStatus.DISCOVERED, text="", workspace=WORKSPACE):
    return SimpleNamespace(id=uuid.uuid4(), workspace_id=workspace, status=status, text=text, version=1)


def _setup(monkeypatch, news_items, *, auto_draft=True, behaviour=None):
    flow = SimpleNamespace(id=uuid.uuid4(), workspace_id=WORKSPACE, auto_draft=auto_draft)
    job = SimpleNamespace(id=uuid.uuid4(), flow_id=flow.id, created_news_ids=[str(n.id) for n in news_items])
    calls, events = [], []

    async def fake_generate(db, *, news, expected_version, user_id):
        calls.append((news.id, expected_version, user_id))
        if behaviour:
            behaviour(news)
        return {}

    async def fake_log(db, *, workspace_id, action, details=None, actor_user_id=None):
        events.append((action, details))

    monkeypatch.setattr(ai_service, "generate_ai_draft", fake_generate)
    monkeypatch.setattr(ai_service, "log_activity", fake_log)
    return FakeDb(flow, news_items), job, calls, events


@pytest.mark.asyncio
async def test_generates_drafts_for_new_materials_as_workspace_owner(monkeypatch):
    items = [_news(), _news()]
    db, job, calls, events = _setup(monkeypatch, items)
    summary = await ai_service.auto_generate_drafts(db, job)
    assert summary == {"generated": 2, "failed": 0, "skipped": 0, "stopped": None}
    assert [c[2] for c in calls] == [OWNER, OWNER]
    assert events[-1][0] == "auto_drafts_completed" and events[-1][1]["generated"] == 2


@pytest.mark.asyncio
async def test_does_nothing_when_flag_off_or_no_new_materials(monkeypatch):
    items = [_news()]
    db, job, calls, _ = _setup(monkeypatch, items, auto_draft=False)
    assert (await ai_service.auto_generate_drafts(db, job))["generated"] == 0 and calls == []
    db, job, calls, _ = _setup(monkeypatch, [])
    assert (await ai_service.auto_generate_drafts(db, job))["generated"] == 0 and calls == []


@pytest.mark.asyncio
async def test_skips_materials_already_edited_in_other_status_or_workspace(monkeypatch):
    items = [_news(text="уже написано"), _news(status=NewsStatus.APPROVED), _news(workspace=uuid.uuid4()), _news()]
    db, job, calls, _ = _setup(monkeypatch, items)
    summary = await ai_service.auto_generate_drafts(db, job)
    assert (summary["generated"], summary["skipped"]) == (1, 3) and len(calls) == 1


@pytest.mark.asyncio
async def test_model_refusal_on_one_material_does_not_stop_the_batch(monkeypatch):
    items = [_news(), _news(), _news()]

    def behaviour(news):
        if news.id == items[1].id:
            raise AppError("VALIDATION_ERROR", "ИИ отказался обрабатывать этот материал")

    db, job, calls, events = _setup(monkeypatch, items, behaviour=behaviour)
    summary = await ai_service.auto_generate_drafts(db, job)
    assert (summary["generated"], summary["failed"], summary["stopped"]) == (2, 1, None)
    failed = [e for e in events if e[0] == "auto_draft_failed"]
    assert len(failed) == 1 and failed[0][1]["news_id"] == str(items[1].id)


@pytest.mark.asyncio
async def test_budget_exhaustion_stops_the_batch(monkeypatch):
    items = [_news(), _news(), _news()]

    def behaviour(news):
        if news.id == items[1].id:
            raise AppError("LLM_BUDGET_EXCEEDED", "Дневной бюджет токенов исчерпан")

    db, job, calls, _ = _setup(monkeypatch, items, behaviour=behaviour)
    summary = await ai_service.auto_generate_drafts(db, job)
    assert summary["stopped"] == "LLM_BUDGET_EXCEEDED" and summary["generated"] == 1 and len(calls) == 2


@pytest.mark.asyncio
async def test_unexpected_error_rolls_back_and_continues(monkeypatch):
    items = [_news(), _news()]

    def behaviour(news):
        if news.id == items[0].id:
            raise RuntimeError("сбой БД")

    db, job, calls, _ = _setup(monkeypatch, items, behaviour=behaviour)
    summary = await ai_service.auto_generate_drafts(db, job)
    assert (summary["generated"], summary["failed"]) == (1, 1) and db.rollbacks == 1


@pytest.mark.asyncio
async def test_batch_is_capped_per_job(monkeypatch):
    items = [_news() for _ in range(ai_service._AUTO_DRAFT_MAX_PER_JOB + 5)]
    db, job, calls, _ = _setup(monkeypatch, items)
    await ai_service.auto_generate_drafts(db, job)
    assert len(calls) == ai_service._AUTO_DRAFT_MAX_PER_JOB
