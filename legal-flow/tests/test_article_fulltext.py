import uuid

import pytest

from app.db import get_session_factory
from app.models import Discovery, NewsItem, ScanJob, Story
from app.models.enums import NewsStatus, ScanJobStatus
from app.providers import html_source
from app.providers.html_source import extract_visible_text, fetch_article_text
from app.providers.http_fetch import AccessLimitedError, FetchResult
from app.services.ai_service import _fact_source_fragment
from tests.conftest import unique_email


def test_extract_visible_text_skips_nav_and_script_keeps_paragraphs():
    html = (
        "<html><head><style>.x{}</style></head><body>"
        "<nav>Меню Главная Контакты</nav>"
        "<article><h1>Заголовок</h1><p>Первый абзац.</p><p>Второй  абзац  с  пробелами.</p></article>"
        "<script>var x = 1;</script>"
        "<footer>© 2026</footer>"
        "</body></html>"
    )
    text = extract_visible_text(html)
    assert "Заголовок" in text
    assert "Первый абзац." in text
    assert "Второй абзац с пробелами." in text
    assert "Меню" not in text
    assert "var x" not in text
    assert "2026" not in text


@pytest.mark.asyncio
async def test_fetch_article_text_returns_parsed_text(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return FetchResult(url, 200, "text/html; charset=utf-8", "<p>Полный текст статьи.</p>".encode("utf-8"))

    monkeypatch.setattr(html_source, "fetch_url", fake_fetch)
    text = await fetch_article_text("https://www.garant.ru/news/1/", domain="garant.ru")
    assert text == "Полный текст статьи."


@pytest.mark.asyncio
async def test_fetch_article_text_returns_none_on_access_limited(monkeypatch):
    async def fake_fetch(url, **kwargs):
        raise AccessLimitedError("blocked", status_code=403)

    monkeypatch.setattr(html_source, "fetch_url", fake_fetch)
    text = await fetch_article_text("https://www.garant.ru/news/1/", domain="garant.ru")
    assert text is None


async def _seed_news_item(authed, *, domain: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Creates a flow + a NewsItem that looks exactly like one HtmlSourceProvider
    would have produced (discovery_domain + discovery_id -> Discovery.normalized_url),
    without going through a real scan_job — this test only cares about what
    _fact_source_fragment does with that shape of row."""
    email = unique_email("fulltext")
    reg = await authed.post("/api/v1/auth/register", json={"name": "F", "email": email, "password": "correct-horse-battery"})
    workspace_id = uuid.UUID(reg.json()["memberships"][0]["workspace_id"])
    flow = await authed.post(
        "/api/v1/flows", json={"name": "F", "theme": "Т", "domains": [domain], "news_limit_per_run": 1}
    )
    flow_id = uuid.UUID(flow.json()["id"])

    factory = get_session_factory()
    async with factory() as db:
        job = ScanJob(flow_id=flow_id, idempotency_key="seed", status=ScanJobStatus.done, provider_name="html")
        db.add(job)
        await db.flush()
        story = Story(workspace_id=workspace_id, story_key="story-1")
        db.add(story)
        await db.flush()
        discovery = Discovery(
            workspace_id=workspace_id, scan_job_id=job.id,
            normalized_url="https://www.garant.ru/news/1/", discovery_domain=domain,
            story_key="story-1", is_duplicate=False, decision_reason="new_story",
        )
        db.add(discovery)
        await db.flush()
        news = NewsItem(
            workspace_id=workspace_id, flow_id=flow_id, story_id=story.id,
            title="Заголовок", text="", status=NewsStatus.DISCOVERED,
            discovery_domain=domain, discovery_original_fragment="короткий анонс",
            discovery_id=discovery.id,
        )
        db.add(news)
        await db.commit()
        return news.id, workspace_id


@pytest.mark.asyncio
async def test_fact_source_fragment_uses_full_article_for_supported_domain(authed, monkeypatch):
    async def fake_fetch_article_text(url, *, domain):
        assert url == "https://www.garant.ru/news/1/"
        assert domain == "garant.ru"
        return "Полный текст статьи с деталями."

    import app.services.ai_service as ai_service
    monkeypatch.setattr(ai_service, "fetch_article_text", fake_fetch_article_text)

    news_id, _ = await _seed_news_item(authed, domain="garant.ru")
    factory = get_session_factory()
    async with factory() as db:
        news = await db.get(NewsItem, news_id)
        fragment = await _fact_source_fragment(db, news)
    assert fragment == "Полный текст статьи с деталями."


@pytest.mark.asyncio
async def test_fact_source_fragment_falls_back_when_fetch_fails(authed, monkeypatch):
    async def fake_fetch_article_text(url, *, domain):
        return None

    import app.services.ai_service as ai_service
    monkeypatch.setattr(ai_service, "fetch_article_text", fake_fetch_article_text)

    news_id, _ = await _seed_news_item(authed, domain="garant.ru")
    factory = get_session_factory()
    async with factory() as db:
        news = await db.get(NewsItem, news_id)
        fragment = await _fact_source_fragment(db, news)
    assert fragment == "короткий анонс"


@pytest.mark.asyncio
async def test_fact_source_fragment_skips_fetch_for_unsupported_domain(authed, monkeypatch):
    async def fake_fetch_article_text(url, *, domain):
        raise AssertionError("should not be called for an unsupported domain")

    import app.services.ai_service as ai_service
    monkeypatch.setattr(ai_service, "fetch_article_text", fake_fetch_article_text)

    news_id, _ = await _seed_news_item(authed, domain="consultant.ru")
    factory = get_session_factory()
    async with factory() as db:
        news = await db.get(NewsItem, news_id)
        fragment = await _fact_source_fragment(db, news)
    assert fragment == "короткий анонс"
