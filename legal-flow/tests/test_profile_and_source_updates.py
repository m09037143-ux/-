import pytest

from app.config import get_settings
from tests.conftest import unique_email

pytestmark = pytest.mark.asyncio


async def test_update_own_display_name(authed):
    email = unique_email("profile")
    await authed.post("/api/v1/auth/register", json={"name": "Old Name", "email": email, "password": "correct-horse-battery"})
    resp = await authed.patch("/api/v1/auth/me", json={"name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"

    me = await authed.get("/api/v1/auth/me")
    assert me.json()["name"] == "New Name"


async def test_toggle_source_active_without_deleting(authed):
    email = unique_email("source-toggle")
    await authed.post("/api/v1/auth/register", json={"name": "S", "email": email, "password": "correct-horse-battery"})
    flow = await authed.post(
        "/api/v1/flows", json={"name": "F", "theme": "Т", "domains": ["garant.ru"], "news_limit_per_run": 1}
    )
    flow_id = flow.json()["id"]
    source_id = flow.json()["sources"][0]["id"]

    resp = await authed.patch(f"/api/v1/flows/{flow_id}/sources/{source_id}", json={"active": False})
    assert resp.status_code == 200
    assert resp.json()["active"] is False
    assert resp.json()["domain"] == "garant.ru"

    listing = await authed.get("/api/v1/flows")
    assert listing.json()[0]["sources"][0]["active"] is False


async def test_sources_report_html_support_and_flow_real_collection_flag(authed, monkeypatch):
    monkeypatch.setattr(get_settings(), "source_fixture_mode", False)
    email = unique_email("source-support")
    await authed.post("/api/v1/auth/register", json={"name": "S", "email": email, "password": "correct-horse-battery"})
    flow = await authed.post(
        "/api/v1/flows",
        json={"name": "F", "theme": "Т", "domains": ["garant.ru", "pravo.ru"], "news_limit_per_run": 1},
    )
    assert flow.json()["real_collection_enabled"] is True
    assert all(s["html_supported"] for s in flow.json()["sources"])


async def test_site_without_collection_method_is_reported_unavailable(authed, monkeypatch):
    """Сайт, для которого не нашли ленту/список, виден в потоке со статусом unavailable и
    причиной, но не отключает реальный сбор для остальных сайтов."""
    monkeypatch.setattr(get_settings(), "source_fixture_mode", False)
    from app.services import flow_service
    from app.services.source_onboarding import OnboardResult

    async def fake_onboard(db, *, domain, confirmed, confirmed_by):
        if domain == "garant.ru":
            return OnboardResult("builtin", "https://www.garant.ru/news/", "ready", robots_checked=True)
        return OnboardResult(None, None, "unavailable", "Не нашли ленту новостей", robots_checked=True)

    monkeypatch.setattr(flow_service, "onboard_source", fake_onboard)
    email = unique_email("source-mixed")
    await authed.post("/api/v1/auth/register", json={"name": "S", "email": email, "password": "correct-horse-battery"})
    flow = await authed.post(
        "/api/v1/flows",
        json={"name": "F", "theme": "Т", "domains": ["garant.ru", "example-legal-news.ru"], "news_limit_per_run": 1, "rights_confirmed": True},
    )
    assert flow.status_code == 201, flow.text
    assert flow.json()["real_collection_enabled"] is True
    by_domain = {s["domain"]: s for s in flow.json()["sources"]}
    assert by_domain["garant.ru"]["html_supported"] is True and by_domain["garant.ru"]["kind"] == "builtin"
    assert by_domain["example-legal-news.ru"]["html_supported"] is False
    assert by_domain["example-legal-news.ru"]["status"] == "unavailable"
    assert by_domain["example-legal-news.ru"]["status_note"] == "Не нашли ленту новостей"


async def test_real_collection_disabled_when_source_fixture_mode_is_default_on(authed):
    email = unique_email("source-fixturemode")
    await authed.post("/api/v1/auth/register", json={"name": "S", "email": email, "password": "correct-horse-battery"})
    flow = await authed.post(
        "/api/v1/flows", json={"name": "F", "theme": "Т", "domains": ["garant.ru"], "news_limit_per_run": 1}
    )
    assert flow.json()["real_collection_enabled"] is False
