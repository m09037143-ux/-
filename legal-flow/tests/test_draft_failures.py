"""Почему не получился автоматический черновик: журнал неудач и понятная причина в списке. Без базы данных."""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.config import Settings
from app.errors import AppError
from app.schemas.news import NewsListItemOut
from app.services import ai_service

WS = uuid.uuid4()


def test_failure_reasons_are_translated_into_plain_language():
    f = ai_service.friendly_failure_reason
    assert f("Ответ модели на этапе 'fact_passport' не прошёл проверку структуры: http_error: ReadTimeout") == "модель не успела ответить"
    assert f("Ответ модели ... invalid_json_or_schema: Invalid JSON") == "модель вернула ответ в неверном формате"
    assert "чувствительная тема" in f("Модель отказалась обрабатывать материал")
    assert f("") == "причина не записана" and f("x" * 500) == "x" * 120


class Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class EventsDb:
    def __init__(self, events):
        self.events, self.statements = events, []

    async def scalars(self, stmt):
        self.statements.append(stmt)
        return Rows(self.events)


def _event(news_id, reason):
    return SimpleNamespace(details={"news_id": str(news_id), "reason": reason})


@pytest.mark.asyncio
async def test_failures_are_counted_per_material_and_show_the_latest_reason():
    a, b = uuid.uuid4(), uuid.uuid4()
    events = [  # от новых к старым, как отдаёт запрос
        _event(a, "invalid_json_or_schema: Invalid JSON"),
        _event(b, "http_error: ReadTimeout"),
        _event(a, "http_error: ReadTimeout"),
    ]
    db = EventsDb(events)
    out = await ai_service.draft_failures(db, workspace_id=WS, news_ids=[a, b, uuid.uuid4()])
    assert out[str(a)] == (2, "модель вернула ответ в неверном формате")  # причина последней неудачи
    assert out[str(b)] == (1, "модель не успела ответить")
    sql = str(db.statements[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "auto_draft_failed" in sql and f"'{a}'" in sql and "ORDER BY activity_events.created_at DESC" in sql


@pytest.mark.asyncio
async def test_no_query_when_there_is_nothing_to_check():
    db = EventsDb([])
    assert await ai_service.draft_failures(db, workspace_id=WS, news_ids=[]) == {}
    assert db.statements == []


def test_list_item_defaults_mean_no_failures():
    item = NewsListItemOut(id=uuid.uuid4(), title="t", discovery_domain="d.ru", status="DISCOVERED", has_official_document=False, created_at="2026-10-15T12:00:00Z")
    assert item.draft_failures == 0 and item.draft_failure_reason == ""


class LogDb:
    def __init__(self):
        self.rollbacks = 0

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_unexpected_error_is_logged_so_the_item_does_not_look_pending_forever(monkeypatch):
    logged = []

    async def fake_log(db, **kw):
        logged.append(kw)

    async def boom(*a, **k):
        raise RuntimeError("что-то сломалось")

    monkeypatch.setattr(ai_service, "log_activity", fake_log)
    monkeypatch.setattr(ai_service, "generate_ai_draft", boom)
    news = SimpleNamespace(id=uuid.uuid4(), workspace_id=WS, version=1)
    summary = {"generated": 0, "failed": 0, "skipped": 0, "stopped": None}
    await ai_service._draft_one(LogDb(), news, uuid.uuid4(), summary)
    assert summary["failed"] == 1 and logged[0]["action"] == "auto_draft_failed" and logged[0]["details"]["news_id"] == str(news.id)


@pytest.mark.asyncio
async def test_model_error_is_logged_and_budget_stop_is_not(monkeypatch):
    logged = []

    async def fake_log(db, **kw):
        logged.append(kw)

    monkeypatch.setattr(ai_service, "log_activity", fake_log)
    news = SimpleNamespace(id=uuid.uuid4(), workspace_id=WS, version=1)

    async def refuse(*a, **k):
        raise AppError("VALIDATION_ERROR", "Модель отказалась обрабатывать материал")

    monkeypatch.setattr(ai_service, "generate_ai_draft", refuse)
    summary = {"generated": 0, "failed": 0, "skipped": 0, "stopped": None}
    await ai_service._draft_one(LogDb(), news, uuid.uuid4(), summary)
    assert summary["failed"] == 1 and len(logged) == 1

    async def over_budget(*a, **k):
        raise AppError("LLM_BUDGET_EXCEEDED", "бюджет")

    monkeypatch.setattr(ai_service, "generate_ai_draft", over_budget)
    stop = {"generated": 0, "failed": 0, "skipped": 0, "stopped": None}
    await ai_service._draft_one(LogDb(), news, uuid.uuid4(), stop)
    assert stop["stopped"] == "LLM_BUDGET_EXCEEDED" and stop["failed"] == 0 and len(logged) == 1


def test_model_wait_is_long_enough_for_the_fallback_model():
    assert Settings(_env_file=None).llm_timeout_seconds >= 60
