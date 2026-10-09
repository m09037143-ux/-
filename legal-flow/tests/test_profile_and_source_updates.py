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


async def test_mixed_domains_disable_real_collection_for_whole_flow(authed, monkeypatch):
    monkeypatch.setattr(get_settings(), "source_fixture_mode", False)
    email = unique_email("source-mixed")
    await authed.post("/api/v1/auth/register", json={"name": "S", "email": email, "password": "correct-horse-battery"})
    # example-legal-news.ru stands in for "a domain HtmlSourceProvider doesn't support
    # yet" — any domain not in html_source.SUPPORTED_DOMAINS works for this test.
    flow = await authed.post(
        "/api/v1/flows",
        json={"name": "F", "theme": "Т", "domains": ["garant.ru", "example-legal-news.ru"], "news_limit_per_run": 1},
    )
    assert flow.json()["real_collection_enabled"] is False
    by_domain = {s["domain"]: s["html_supported"] for s in flow.json()["sources"]}
    assert by_domain == {"garant.ru": True, "example-legal-news.ru": False}


async def test_real_collection_disabled_when_source_fixture_mode_is_default_on(authed):
    email = unique_email("source-fixturemode")
    await authed.post("/api/v1/auth/register", json={"name": "S", "email": email, "password": "correct-horse-battery"})
    flow = await authed.post(
        "/api/v1/flows", json={"name": "F", "theme": "Т", "domains": ["garant.ru"], "news_limit_per_run": 1}
    )
    assert flow.json()["real_collection_enabled"] is False
