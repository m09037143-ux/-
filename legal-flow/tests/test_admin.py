import pytest
from sqlalchemy import select

from app.db import get_session_factory
from app.models import User
from tests.conftest import unique_email

pytestmark = pytest.mark.asyncio


async def _make_platform_admin(email: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        user = await db.scalar(select(User).where(User.email == email))
        user.is_platform_admin = True
        await db.commit()


async def test_non_admin_cannot_access_admin_endpoints(authed):
    email = unique_email("regular")
    await authed.post("/api/v1/auth/register", json={"name": "R", "email": email, "password": "correct-horse-battery"})
    resp = await authed.get("/api/v1/admin/source-policies")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_admin_can_list_and_create_source_policy(authed):
    email = unique_email("admin")
    await authed.post("/api/v1/auth/register", json={"name": "A", "email": email, "password": "correct-horse-battery"})
    await _make_platform_admin(email.lower())

    listing = await authed.get("/api/v1/admin/source-policies")
    assert listing.status_code == 200
    domains = {p["domain"] for p in listing.json()}
    assert "garant.ru" in domains and "pravo.ru" in domains

    create = await authed.post(
        "/api/v1/admin/source-policies",
        json={
            "domain": "novoedelo.ru",
            "basis": "Тестовое согласование",
            "responsible": "platform_admin",
            "allowed_actions": ["discover", "fetch", "extract_facts", "temporary_store"],
        },
    )
    assert create.status_code == 201, create.text
    assert create.json()["domain"] == "novoedelo.ru"


async def test_admin_rejects_unknown_action(authed):
    email = unique_email("admin2")
    await authed.post("/api/v1/auth/register", json={"name": "A2", "email": email, "password": "correct-horse-battery"})
    await _make_platform_admin(email.lower())

    resp = await authed.post(
        "/api/v1/admin/source-policies",
        json={"domain": "example-legal.ru", "basis": "b", "responsible": "r", "allowed_actions": ["hack_everything"]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_admin_can_view_users_and_plans(authed):
    email = unique_email("admin3")
    await authed.post("/api/v1/auth/register", json={"name": "A3", "email": email, "password": "correct-horse-battery"})
    await _make_platform_admin(email.lower())

    users = await authed.get("/api/v1/admin/users")
    assert users.status_code == 200
    assert any(u["email"] == email.lower() for u in users.json())

    plans = await authed.get("/api/v1/admin/plans")
    assert plans.status_code == 200
    assert len(plans.json()) == 3


async def test_admin_route_requires_authentication(client):
    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 401
