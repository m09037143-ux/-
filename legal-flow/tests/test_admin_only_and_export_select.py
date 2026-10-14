"""Закрытый режим (вход только администратору) и выбор материалов для XML-экспорта. Без базы данных."""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app import deps
from app.errors import AppError
from app.schemas.exports import CreateExportRequest
from app.services import auth_service, export_service

ROOT = Path(__file__).resolve().parent.parent


def _settings(admin_only: bool):
    return SimpleNamespace(admin_only_mode=admin_only, session_ttl_hours=24, trial_hours=72, trial_plan_code="start")


def _user(is_admin: bool):
    return SimpleNamespace(id=uuid.uuid4(), is_platform_admin=is_admin, password_hash="x", name="N", email="a@b.ru")


class LoginDb:
    def __init__(self, user):
        self.user, self.added, self.commits = user, [], 0

    async def scalar(self, *_a, **_k):
        return self.user

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def get(self, model, key):
        return self.user


# ---------------- закрытый режим ----------------

def test_registration_is_closed_in_admin_only_mode(monkeypatch):
    monkeypatch.setattr(auth_service, "get_settings", lambda: _settings(True))
    with pytest.raises(AppError) as exc:
        auth_service.ensure_registration_open()
    assert exc.value.code == "FORBIDDEN" and "в разработке" in exc.value.message


def test_registration_stays_open_by_default(monkeypatch):
    monkeypatch.setattr(auth_service, "get_settings", lambda: _settings(False))
    auth_service.ensure_registration_open()  # не бросает


@pytest.mark.asyncio
async def test_register_service_itself_refuses_in_admin_only_mode(monkeypatch):
    monkeypatch.setattr(auth_service, "get_settings", lambda: _settings(True))
    db = LoginDb(None)
    with pytest.raises(AppError) as exc:
        await auth_service.register(db, name="Иван", email="i@example.com", password="correct-horse-battery")
    assert exc.value.code == "FORBIDDEN" and db.added == []


@pytest.mark.asyncio
async def test_regular_user_cannot_log_in_but_admin_can(monkeypatch):
    monkeypatch.setattr(auth_service, "get_settings", lambda: _settings(True))
    monkeypatch.setattr(auth_service, "verify_password", lambda stored, given: True)

    regular_db = LoginDb(_user(False))
    with pytest.raises(AppError) as exc:
        await auth_service.login(regular_db, email="u@example.com", password="whatever-pass")
    assert exc.value.code == "FORBIDDEN" and "в разработке" in exc.value.message
    assert regular_db.added == [] and regular_db.commits == 0  # сессия не создаётся

    admin = _user(True)
    user, token = await auth_service.login(LoginDb(admin), email="m@example.com", password="whatever-pass")
    assert user is admin and token


@pytest.mark.asyncio
async def test_wrong_password_is_still_a_plain_login_error(monkeypatch):
    monkeypatch.setattr(auth_service, "get_settings", lambda: _settings(True))
    monkeypatch.setattr(auth_service, "verify_password", lambda stored, given: False)
    with pytest.raises(AppError) as exc:
        await auth_service.login(LoginDb(_user(True)), email="m@example.com", password="bad")
    assert exc.value.code == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_old_session_of_regular_user_stops_working_in_admin_only_mode(monkeypatch):
    session = SimpleNamespace(user_id=uuid.uuid4())
    monkeypatch.setattr(deps, "get_settings", lambda: _settings(True))
    with pytest.raises(AppError) as exc:
        await deps.get_current_user(session=session, db=LoginDb(_user(False)))
    assert exc.value.code == "UNAUTHORIZED" and "в разработке" in exc.value.message

    admin = _user(True)
    assert await deps.get_current_user(session=session, db=LoginDb(admin)) is admin

    monkeypatch.setattr(deps, "get_settings", lambda: _settings(False))
    regular = _user(False)
    assert await deps.get_current_user(session=session, db=LoginDb(regular)) is regular


def test_admin_only_mode_is_enabled_in_the_production_template():
    template = (ROOT / "deploy" / ".env.production.example").read_text(encoding="utf-8")
    assert "ADMIN_ONLY_MODE=true" in template


# ---------------- выбор материалов для экспорта ----------------

class Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class SelectDb:
    def __init__(self, rows):
        self.rows, self.statements = rows, []

    async def scalars(self, stmt):
        self.statements.append(stmt)
        return Rows(self.rows)


def _news(minutes_ago: int):
    now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(id=uuid.uuid4(), published_at=now - timedelta(minutes=minutes_ago), created_at=now - timedelta(days=1))


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_default_export_takes_the_latest_published_items_newest_first():
    db = SelectDb([_news(1)])
    await export_service._select_items(db, workspace_id=uuid.uuid4(), mode="latest", limit=3, news_item_ids=None)
    sql = _sql(db.statements[0])
    assert "status = 'PUBLISHED'" in sql
    assert "ORDER BY coalesce(news_items.published_at, news_items.created_at) DESC" in sql
    assert sql.rstrip().endswith("LIMIT 3")


@pytest.mark.asyncio
async def test_latest_mode_falls_back_to_the_default_count_when_limit_is_zero():
    db = SelectDb([])
    await export_service._select_items(db, workspace_id=uuid.uuid4(), mode="latest", limit=0, news_item_ids=None)
    assert _sql(db.statements[0]).rstrip().endswith(f"LIMIT {export_service.DEFAULT_LATEST_COUNT}")


@pytest.mark.asyncio
async def test_all_mode_has_a_hard_cap():
    db = SelectDb([])
    await export_service._select_items(db, workspace_id=uuid.uuid4(), mode="all", limit=5, news_item_ids=None)
    assert _sql(db.statements[0]).rstrip().endswith(f"LIMIT {export_service.MAX_EXPORT_ITEMS}")


@pytest.mark.asyncio
async def test_selected_items_are_returned_newest_first_and_only_published_of_this_workspace():
    old, mid, new = _news(300), _news(120), _news(5)
    db = SelectDb([old, new, mid])  # из базы порядок произвольный
    rows = await export_service._select_items(
        db, workspace_id=uuid.uuid4(), mode="selected", limit=10, news_item_ids=[old.id, new.id, mid.id, old.id]
    )
    assert rows == [new, mid, old]
    sql = _sql(db.statements[0])
    assert "status = 'PUBLISHED'" in sql and "workspace_id" in sql and " IN (" in sql


@pytest.mark.asyncio
async def test_selected_items_that_are_missing_or_unpublished_are_refused():
    only_one = _news(5)
    db = SelectDb([only_one])  # из двух запрошенных нашёлся один
    with pytest.raises(AppError) as exc:
        await export_service._select_items(
            db, workspace_id=uuid.uuid4(), mode="selected", limit=10, news_item_ids=[only_one.id, uuid.uuid4()]
        )
    assert exc.value.code == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_selected_mode_without_a_choice_is_refused():
    with pytest.raises(AppError) as exc:
        await export_service._select_items(SelectDb([]), workspace_id=uuid.uuid4(), mode="selected", limit=10, news_item_ids=[])
    assert exc.value.code == "VALIDATION_ERROR" and "хотя бы один" in exc.value.message


@pytest.mark.asyncio
async def test_candidates_list_is_newest_first():
    db = SelectDb([])
    await export_service.list_candidates(db, workspace_id=uuid.uuid4(), limit=50)
    sql = _sql(db.statements[0])
    assert "coalesce(news_items.published_at, news_items.created_at) DESC" in sql and "LIMIT 50" in sql


def test_export_request_defaults_and_limits():
    req = CreateExportRequest()
    assert req.mode == "latest" and req.limit == 10 and req.news_item_ids is None and req.filename == "legal-news.xml"
    assert CreateExportRequest(filename="a.xml").mode == "latest"  # старый вызов API без новых полей работает
    for bad in ({"limit": 0}, {"limit": 201}, {"mode": "everything"}, {"news_item_ids": ["not-a-uuid"]}):
        with pytest.raises(ValidationError):
            CreateExportRequest(**bad)
