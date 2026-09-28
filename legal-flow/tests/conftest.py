import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://pravovoy_potok:dev_local_only@localhost:5432/pravovoy_potok_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("CSRF_SECRET", "test-secret")
os.environ.setdefault("PAYMENT_PROVIDER", "fake")
os.environ.setdefault("PAYMENT_WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("LLM_FIXTURE_MODE", "true")

from app.config import get_settings  # noqa: E402
from app.db import Base, reset_engine_for_tests, get_engine  # noqa: E402
from app.redis_client import reset_redis_for_tests, get_redis  # noqa: E402
from app.main import create_app  # noqa: E402
import app.models  # noqa: E402,F401


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _prepare_database():
    get_settings.cache_clear()
    settings = get_settings()
    await reset_engine_for_tests(settings.database_url)
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await _seed_plans_and_policies()
    yield
    await engine.dispose()


async def _seed_plans_and_policies():
    from migrations.versions.a23b06f68438_seed_plans_and_default_source_policies import (
        PLANS,
        DEFAULT_POLICIES,
    )
    from app.models import Plan, SourcePolicy
    from app.db import get_session_factory
    from datetime import datetime, timezone

    factory = get_session_factory()
    async with factory() as session:
        for p in PLANS:
            session.add(Plan(code=p["code"], name=p["name"], price_rub=p["price_rub"], limits=p["limits"]))
        for p in DEFAULT_POLICIES:
            session.add(
                SourcePolicy(
                    domain=p["domain"],
                    material_path="",
                    reviewed_at=datetime.now(timezone.utc),
                    basis=p["basis"],
                    responsible=p["responsible"],
                    allowed_actions=p["allowed_actions"],
                    attribution_required=p["attribution_required"],
                    attribution_text="",
                )
            )
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def _flush_redis():
    r = get_redis()
    await r.flushdb()
    yield
    await r.flushdb()


@pytest_asyncio.fixture()
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


class AuthedClient:
    """Thin wrapper that keeps the CSRF header in sync with the csrf cookie the
    server just set, mirroring what a real browser + JS front-end would do."""

    def __init__(self, client: AsyncClient):
        self.client = client

    def _headers(self) -> dict:
        settings = get_settings()
        csrf = self.client.cookies.get(settings.csrf_cookie_name)
        return {"X-CSRF-Token": csrf} if csrf else {}

    async def get(self, url, **kw):
        return await self.client.get(url, **kw)

    async def post(self, url, **kw):
        headers = {**self._headers(), **kw.pop("headers", {})}
        return await self.client.post(url, headers=headers, **kw)

    async def patch(self, url, **kw):
        headers = {**self._headers(), **kw.pop("headers", {})}
        return await self.client.patch(url, headers=headers, **kw)

    async def put(self, url, **kw):
        headers = {**self._headers(), **kw.pop("headers", {})}
        return await self.client.put(url, headers=headers, **kw)

    async def delete(self, url, **kw):
        headers = {**self._headers(), **kw.pop("headers", {})}
        return await self.client.delete(url, headers=headers, **kw)


@pytest_asyncio.fixture()
async def authed(client):
    return AuthedClient(client)


def unique_email(prefix: str = "user") -> str:
    # email-validator treats .test as a reserved TLD; .com passes syntax validation
    # without doing a real deliverability/DNS check (check_deliverability defaults off).
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


async def run_next_pending_job():
    """Runs the exact function the real worker process (app/worker/runner.py) calls:
    finds the oldest pending scan_job by polling the table directly (no separate
    queue) and processes it. This is the integration seam tests exercise."""
    from app.db import get_session_factory
    from app.services.scan_service import process_scan_job
    from app.worker.runner import fetch_next_pending_job_id

    factory = get_session_factory()
    async with factory() as db:
        job_id = await fetch_next_pending_job_id(db)
        assert job_id is not None, "expected a scan job to be pending"
        return await process_scan_job(db, job_id)
