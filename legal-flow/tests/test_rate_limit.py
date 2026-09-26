import pytest

from app.config import get_settings
from tests.conftest import unique_email

pytestmark = pytest.mark.asyncio


async def test_register_rate_limit_blocks_after_threshold(client):
    settings = get_settings()
    limit = settings.rate_limit_register_per_hour

    for _ in range(limit):
        resp = await client.post(
            "/api/v1/auth/register",
            json={"name": "R", "email": unique_email("rl"), "password": "correct-horse-battery"},
        )
        assert resp.status_code == 201

    blocked = await client.post(
        "/api/v1/auth/register",
        json={"name": "R", "email": unique_email("rl"), "password": "correct-horse-battery"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


async def test_login_rate_limit_is_per_email(authed):
    email = unique_email("rl-login")
    await authed.post("/api/v1/auth/register", json={"name": "L", "email": email, "password": "correct-horse-battery"})
    await authed.post("/api/v1/auth/logout")

    settings = get_settings()
    for _ in range(settings.rate_limit_login_per_15min):
        await authed.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})

    blocked = await authed.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
    assert blocked.status_code == 429
