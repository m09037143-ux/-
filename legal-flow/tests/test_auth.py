import pytest

from tests.conftest import unique_email

pytestmark = pytest.mark.asyncio


async def test_register_creates_workspace_and_starts_trial(authed):
    email = unique_email()
    resp = await authed.post(
        "/api/v1/auth/register",
        json={"name": "Иван Иванов", "email": email, "password": "correct-horse-battery"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == email
    assert len(body["memberships"]) == 1
    assert body["memberships"][0]["role"] == "workspace_owner"

    access = await authed.get("/api/v1/access")
    assert access.status_code == 200
    access_body = access.json()
    assert access_body["trial_state"] == "active"
    assert access_body["can_mutate"] is True
    assert access_body["trial_ends_at"] is not None


async def test_register_rejects_duplicate_email(authed):
    email = unique_email()
    r1 = await authed.post(
        "/api/v1/auth/register", json={"name": "A", "email": email, "password": "correct-horse-battery"}
    )
    assert r1.status_code == 201

    r2 = await authed.post(
        "/api/v1/auth/register", json={"name": "B", "email": email, "password": "another-password"}
    )
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_login_wrong_password_rejected_and_hash_is_argon2(authed):
    from sqlalchemy import select
    from app.db import get_session_factory
    from app.models import User

    email = unique_email()
    await authed.post(
        "/api/v1/auth/register", json={"name": "C", "email": email, "password": "correct-horse-battery"}
    )

    factory = get_session_factory()
    async with factory() as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user.password_hash.startswith("$argon2id$")
        assert user.password_hash != "correct-horse-battery"

    bad = await authed.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "UNAUTHORIZED"


async def test_me_requires_session(authed):
    resp = await authed.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_logout_requires_csrf_header(client):
    email = unique_email()
    await client.post(
        "/api/v1/auth/register", json={"name": "D", "email": email, "password": "correct-horse-battery"}
    )
    # No X-CSRF-Token header attached deliberately.
    resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_logout_then_me_fails(authed):
    email = unique_email()
    await authed.post(
        "/api/v1/auth/register", json={"name": "E", "email": email, "password": "correct-horse-battery"}
    )
    out = await authed.post("/api/v1/auth/logout")
    assert out.status_code == 204

    me = await authed.get("/api/v1/auth/me")
    assert me.status_code == 401


async def test_forgot_and_reset_password_flow(authed):
    email = unique_email()
    await authed.post(
        "/api/v1/auth/register", json={"name": "F", "email": email, "password": "old-password-123"}
    )
    await authed.post("/api/v1/auth/logout")

    forgot = await authed.post("/api/v1/auth/forgot-password", json={"email": email})
    assert forgot.status_code == 202
    token = forgot.json()["dev_reset_token"]

    reset = await authed.post(
        "/api/v1/auth/reset-password", json={"token": token, "new_password": "brand-new-password-456"}
    )
    assert reset.status_code == 204

    bad_login = await authed.post("/api/v1/auth/login", json={"email": email, "password": "old-password-123"})
    assert bad_login.status_code == 401

    good_login = await authed.post(
        "/api/v1/auth/login", json={"email": email, "password": "brand-new-password-456"}
    )
    assert good_login.status_code == 200


async def test_forgot_password_unknown_email_does_not_leak(authed):
    resp = await authed.post("/api/v1/auth/forgot-password", json={"email": unique_email("ghost")})
    assert resp.status_code == 202
    assert "dev_reset_token" not in resp.json()
