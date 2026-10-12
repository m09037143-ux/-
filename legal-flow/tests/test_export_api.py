import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app import deps
from app.errors import AppError
from app.models.enums import ReleaseMode
from app.routers.exports import attachment_headers
from app.services import api_key_service, feed_service
from app.services.export_service import render_xml

WS = uuid.uuid4()


def _request(headers: dict[str, str]) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})


# ---------------- ключи ----------------

def test_generated_keys_are_long_random_prefixed_and_hashed_deterministically():
    a, b = api_key_service.generate_key(), api_key_service.generate_key()
    assert a != b and a.startswith("pp_live_") and len(a) >= 40
    assert api_key_service.hash_key(a) == api_key_service.hash_key(a) and len(api_key_service.hash_key(a)) == 64
    assert api_key_service.display_prefix(a) == a[:12] and a not in api_key_service.display_prefix(a) + "…"


class KeyDb:
    def __init__(self, scalar_value=None, existing=None):
        self.scalar_value = scalar_value
        self.existing = existing
        self.added, self.commits = [], 0

    async def scalar(self, *_a, **_k):
        return self.scalar_value

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def get(self, model, key):
        return self.existing


async def _noop_log(db, **kw):
    return None


@pytest.mark.asyncio
async def test_create_key_stores_only_the_hash_and_returns_the_raw_key_once(monkeypatch):
    monkeypatch.setattr(api_key_service, "log_activity", _noop_log)
    db = KeyDb(scalar_value=0)
    key, raw = await api_key_service.create_key(db, workspace_id=WS, user_id=uuid.uuid4(), name="  Сайт редакции  ")
    assert db.added == [key] and key.name == "Сайт редакции"
    assert key.key_hash == api_key_service.hash_key(raw) and key.key_prefix == raw[:12]
    assert raw not in {str(v) for v in vars(key).values()}  # сам ключ в записи отсутствует


@pytest.mark.asyncio
async def test_create_key_respects_the_active_key_limit(monkeypatch):
    monkeypatch.setattr(api_key_service, "log_activity", _noop_log)
    with pytest.raises(AppError) as exc:
        await api_key_service.create_key(KeyDb(scalar_value=10), workspace_id=WS, user_id=uuid.uuid4(), name="ещё один")
    assert exc.value.code == "PLAN_LIMIT"


@pytest.mark.asyncio
async def test_revoke_key_is_idempotent_and_scoped_to_the_workspace(monkeypatch):
    monkeypatch.setattr(api_key_service, "log_activity", _noop_log)
    key = SimpleNamespace(id=uuid.uuid4(), workspace_id=WS, name="k", revoked_at=None)
    db = KeyDb(existing=key)
    await api_key_service.revoke_key(db, workspace_id=WS, key_id=key.id, user_id=uuid.uuid4())
    assert key.revoked_at is not None and db.commits == 1
    await api_key_service.revoke_key(db, workspace_id=WS, key_id=key.id, user_id=uuid.uuid4())
    assert db.commits == 1  # повторный отзыв ничего не пишет
    with pytest.raises(AppError) as exc:  # чужой ключ — «не найден»
        await api_key_service.revoke_key(KeyDb(existing=SimpleNamespace(workspace_id=uuid.uuid4())), workspace_id=WS, key_id=key.id, user_id=uuid.uuid4())
    assert exc.value.code == "NOT_FOUND"


@pytest.mark.asyncio
async def test_authenticate_rejects_foreign_format_and_throttles_last_used_writes():
    db = KeyDb(scalar_value=None)
    assert await api_key_service.authenticate(db, "not-our-key") is None  # до базы не доходит
    raw = api_key_service.generate_key()
    assert await api_key_service.authenticate(KeyDb(scalar_value=None), raw) is None
    fresh = SimpleNamespace(last_used_at=datetime.now(timezone.utc))
    db2 = KeyDb(scalar_value=fresh)
    assert await api_key_service.authenticate(db2, raw) is fresh and db2.commits == 0  # недавно использовался — не пишем
    stale = SimpleNamespace(last_used_at=datetime.now(timezone.utc) - timedelta(hours=1))
    db3 = KeyDb(scalar_value=stale)
    await api_key_service.authenticate(db3, raw)
    assert db3.commits == 1 and stale.last_used_at > datetime.now(timezone.utc) - timedelta(minutes=1)


# ---------------- заголовки запроса ----------------

@pytest.mark.asyncio
async def test_api_key_dependency_accepts_bearer_and_x_api_key_and_rate_limits_per_key(monkeypatch):
    key = SimpleNamespace(id=uuid.uuid4(), workspace_id=WS)
    seen, limited = [], []

    async def fake_auth(db, raw):
        seen.append(raw)
        return key if raw == "pp_live_good" else None

    async def fake_limit(bucket, ident, limit, window):
        limited.append((bucket, ident))

    monkeypatch.setattr(api_key_service, "authenticate", fake_auth)
    from app.services import rate_limit

    monkeypatch.setattr(rate_limit, "enforce_rate_limit", fake_limit)
    assert await deps.get_api_key(_request({"Authorization": "Bearer pp_live_good"}), db=None) is key
    assert await deps.get_api_key(_request({"X-API-Key": "pp_live_good"}), db=None) is key
    assert limited == [("api_key", str(key.id))] * 2
    with pytest.raises(AppError) as no_key:
        await deps.get_api_key(_request({}), db=None)
    assert no_key.value.code == "UNAUTHORIZED"
    with pytest.raises(AppError) as bad:
        await deps.get_api_key(_request({"Authorization": "Bearer pp_live_wrong"}), db=None)
    assert bad.value.code == "UNAUTHORIZED" and "недействителен" in bad.value.message


# ---------------- лента и XML ----------------

def _news(mode=ReleaseMode.INDEPENDENT_FACT_REPORT):
    return SimpleNamespace(
        id=uuid.uuid4(), title="Заголовок & «кавычки»", text="Текст <b>материала</b>", release_mode=mode,
        attribution_owner="Источник", attribution_text="Материал предоставлен", published_at=datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc),
        discovery_domain="pravo.ru", discovery_original_fragment="ВНУТРЕННИЙ ФРАГМЕНТ ИСТОЧНИКА",
    )


def _doc():
    return SimpleNamespace(title="Постановление", url="https://example.gov/doc/1", requisites="№ 1 от 01.10.2026")


def test_feed_item_never_exposes_discovery_source_and_attribution_only_when_required():
    item = feed_service.feed_item(_news(), _doc())
    dumped = json.dumps(item, ensure_ascii=False)
    assert "pravo.ru" not in dumped and "ВНУТРЕННИЙ ФРАГМЕНТ" not in dumped
    assert item["official_document"]["requisites"] == "№ 1 от 01.10.2026" and item["attribution"] is None
    assert item["published_at"] == "2026-10-12T09:00:00+00:00"
    attributed = feed_service.feed_item(_news(ReleaseMode.ATTRIBUTED_REUSE), None)
    assert attributed["attribution"] == {"owner": "Источник", "text": "Материал предоставлен"} and attributed["official_document"] is None


def test_xml_and_json_carry_the_same_materials_and_xml_is_escaped():
    pairs = [(_news(), _doc()), (_news(ReleaseMode.ATTRIBUTED_REUSE), None)]
    xml = render_xml(pairs)
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>') and xml.count("<item>") == 2
    for news, _doc_ in pairs:
        assert str(news.id) in xml and feed_service.feed_item(news, None)["id"] == str(news.id)
    assert "&lt;b&gt;материала&lt;/b&gt;" in xml and "&amp;" in xml
    assert "pravo.ru" not in xml and "ВНУТРЕННИЙ ФРАГМЕНТ" not in xml


# ---------------- скачивание и маршруты ----------------

def test_attachment_headers_support_cyrillic_file_names():
    h = attachment_headers("новости октября.xml")["Content-Disposition"]
    assert h.startswith("attachment;") and 'filename="' in h and "filename*=UTF-8''" in h
    assert "%D0%BD%D0%BE%D0%B2%D0%BE%D1%81%D1%82%D0%B8" in h  # «новости» в UTF-8
    assert 'filename="_' in h  # ASCII-запас без кириллицы
    assert attachment_headers("legal-news.xml")["Content-Disposition"].count("legal-news.xml") == 2


def test_new_routes_are_registered():
    from app.main import create_app

    paths = create_app().openapi()["paths"]
    for path in ("/api/v1/feed", "/api/v1/feed.xml", "/api/v1/feed/{news_id}", "/api/v1/api-keys",
                 "/api/v1/api-keys/{key_id}", "/api/v1/exports/{export_id}/file"):
        assert path in paths, path
    assert "post" in paths["/api/v1/api-keys"] and "delete" in paths["/api/v1/api-keys/{key_id}"]
    assert "post" not in paths["/api/v1/feed"] and "put" not in paths["/api/v1/feed"]  # лента только для чтения


# ---------------- настоящие HTTP-ответы ленты (приложение целиком, БД подставная) ----------------

class FeedDb:
    """Ответы по порядку: ключ, затем официальный документ на каждый материал; список материалов — scalars."""

    def __init__(self, key, news_items, docs):
        self.scalar_results = [key, *docs]
        self.news_items = news_items
        self.commits = 0

    async def scalar(self, *_a, **_k):
        return self.scalar_results.pop(0) if self.scalar_results else None

    async def scalars(self, *_a, **_k):
        return SimpleNamespace(all=lambda: self.news_items)

    async def commit(self):
        self.commits += 1

    async def get(self, *_a, **_k):
        return None


async def _client(db, monkeypatch):
    import httpx

    from app.db import get_db
    from app.main import create_app
    from app.services import rate_limit

    async def no_limit(*a, **k):
        return None

    monkeypatch.setattr(rate_limit, "enforce_rate_limit", no_limit)
    app = create_app()

    async def override():
        yield db

    app.dependency_overrides[get_db] = override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _key():
    return SimpleNamespace(id=uuid.uuid4(), workspace_id=WS, last_used_at=datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_feed_json_over_http_returns_published_items_and_never_the_source(monkeypatch):
    raw = api_key_service.generate_key()
    news = _news()
    db = FeedDb(_key(), [news], [_doc()])
    async with await _client(db, monkeypatch) as client:
        resp = await client.get("/api/v1/feed?limit=10", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1 and body["has_more"] is False
    item = body["items"][0]
    assert item["id"] == str(news.id) and item["official_document"]["title"] == "Постановление"
    assert "pravo.ru" not in resp.text and "ВНУТРЕННИЙ ФРАГМЕНТ" not in resp.text


@pytest.mark.asyncio
async def test_feed_xml_over_http_has_xml_content_type_and_has_more_header(monkeypatch):
    raw = api_key_service.generate_key()
    db = FeedDb(_key(), [_news(), _news()], [_doc(), None])
    async with await _client(db, monkeypatch) as client:
        resp = await client.get("/api/v1/feed.xml?limit=1", headers={"X-API-Key": raw})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/xml")
    assert resp.headers["x-has-more"] == "true"  # материалов больше, чем limit
    assert resp.text.startswith("<?xml") and resp.text.count("<item>") == 1


@pytest.mark.asyncio
async def test_feed_requires_a_valid_key_over_http(monkeypatch):
    db = FeedDb(None, [], [])
    async with await _client(db, monkeypatch) as client:
        no_key = await client.get("/api/v1/feed")
        bad = await client.get("/api/v1/feed", headers={"Authorization": "Bearer pp_live_wrong"})
        write = await client.post("/api/v1/feed", headers={"Authorization": "Bearer pp_live_x"})
    assert no_key.status_code == 401 and bad.status_code == 401
    assert write.status_code == 405  # лента только для чтения


@pytest.mark.asyncio
async def test_frontend_files_are_served_with_no_cache_so_deploys_show_up_without_ctrl_f5():
    import httpx

    from app.main import create_app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        for path in ("/app.js", "/"):
            resp = await client.get(path)
            assert resp.status_code == 200, path
            assert resp.headers["cache-control"] == "no-cache", path
            assert "etag" in resp.headers  # по ETag браузер получит 304, если файл не менялся
        again = await client.get("/app.js", headers={"If-None-Match": resp.headers["etag"]})
    assert again.status_code in (200, 304)
