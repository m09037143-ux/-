import uuid
from pathlib import Path

import pytest

from app.db import get_session_factory
from app.models import Discovery, NewsItem, ScanJob, Story
from app.models.enums import NewsStatus, ScanJobStatus
from app.providers import html_source
from app.providers.html_source import ARTICLE_PARSERS, extract_visible_text, fetch_article_text
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
    page = (
        "<html><body><nav>Меню</nav><article>"
        "<p>Первый абзац полного текста статьи, достаточно длинный, чтобы считаться содержательным абзацем.</p>"
        "<p>Второй абзац полного текста статьи, тоже достаточно длинный для того, чтобы попасть в результат.</p>"
        "</article><footer>Подвал сайта</footer></body></html>"
    )

    async def fake_fetch(url, **kwargs):
        return FetchResult(url, 200, "text/html; charset=utf-8", page.encode("utf-8"))

    async def allow_all(url):
        return True

    monkeypatch.setattr(html_source, "fetch_url", fake_fetch)
    monkeypatch.setattr(html_source, "robots_allows", allow_all)
    # домен без записи в ARTICLE_PARSERS — работает универсальный разбор тела статьи
    text = await fetch_article_text("https://example.com/news/1/", domain="example.com")
    assert text is not None and text.startswith("Первый абзац") and "Второй абзац" in text
    assert "Меню" not in text and "Подвал" not in text


@pytest.mark.asyncio
async def test_fetch_article_text_returns_none_on_access_limited(monkeypatch):
    async def fake_fetch(url, **kwargs):
        raise AccessLimitedError("blocked", status_code=403)

    monkeypatch.setattr(html_source, "fetch_url", fake_fetch)
    text = await fetch_article_text("https://www.garant.ru/news/1/", domain="garant.ru")
    assert text is None


async def _seed_news_item(authed, *, domain: str, live: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    """Creates a flow + a NewsItem that looks exactly like one HtmlSourceProvider
    would have produced (discovery_domain + discovery_id -> Discovery.normalized_url),
    without going through a real scan_job — this test only cares about what
    _fact_source_fragment does with that shape of row."""
    email = unique_email("fulltext")
    reg = await authed.post("/api/v1/auth/register", json={"name": "F", "email": email, "password": "correct-horse-battery"})
    workspace_id = uuid.UUID(reg.json()["memberships"][0]["workspace_id"])
    flow = await authed.post(
        "/api/v1/flows", json={"name": "F", "theme": "Т", "domains": ["garant.ru"], "news_limit_per_run": 1}
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
            metadata_json={"label": "LIVE_LISTING" if live else "DEMO_FIXTURE"},
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
async def test_fact_source_fragment_skips_fetch_for_demo_fixture_items(authed, monkeypatch):
    async def fake_fetch_article_text(url, *, domain):
        raise AssertionError("should not be called for a DEMO_FIXTURE item")

    import app.services.ai_service as ai_service
    monkeypatch.setattr(ai_service, "fetch_article_text", fake_fetch_article_text)

    news_id, _ = await _seed_news_item(authed, domain="garant.ru", live=False)
    factory = get_session_factory()
    async with factory() as db:
        news = await db.get(NewsItem, news_id)
        fragment = await _fact_source_fragment(db, news)
    assert fragment == "короткий анонс"


# --- точный разбор тела статьи на сохранённой реальной разметке (без сети) ---
# Страницы сохранены в октябре 2026 без script/style/svg, но с меню, подвалом, врезками и
# «связанными новостями» — чтобы тест проверял именно исключение этих блоков.
FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_garant_article_parser_keeps_paragraphs_and_list_drops_chrome():
    text = ARTICLE_PARSERS["garant.ru"](_fixture("garant_article_2262611.html"))
    assert text.startswith("В некоторых ситуациях собственнику квартиры или жилого дома")
    assert "Адресную справку чаще всего запрашивают для:" in text
    assert "- совершения сделки купли-продажи недвижимости на вторичном рынке;" in text
    assert "- оформления ипотеки на вторичное жилье;" in text
    # меню/шапка, теги, источник, соцсети, подписка, подпись к фото — не тело статьи
    for junk in ("Теги:", "Источник:", "Перепечатка", "Читать ГАРАНТ.РУ", "Подписаться", "Вакансии", "Демо-доступ", "Фотобанк"):
        assert junk not in text, junk


def test_pravo_article_parser_drops_embedded_related_block_and_chrome():
    text = ARTICLE_PARSERS["pravo.ru"](_fixture("pravo_article_266117.html"))
    assert text.startswith("Минэкономики Германии ищет юристов для сопровождения продажи SEFE")
    assert "Подготовкой этой операции занимается банк Lazard." in text
    # врезка «читайте также» посреди статьи, лид из шапки, кнопки «Поделиться», теги — исключены
    assert "Суд оставил в силе запрет на продолжение чешского арбитража" not in text
    assert "Поделиться" not in text
    assert "Article tags" not in text
    assert "Санкции" not in text


def test_consultant_article_parser_keeps_body_and_document_block_drops_related_news():
    text = ARTICLE_PARSERS["consultant.ru"](_fixture("consultant_article_32769.html"))
    assert text.startswith("С 13 октября 2026 года при форс-мажоре заказчики смогут менять условия федеральных контрактов")
    assert "- срок исполнения контракта;" in text
    assert "Есть и другие изменения." in text
    # блок «Документ:» остаётся в тексте как есть (из него факты достаёт отдельный этап)
    assert "Документ:" in text
    assert "Постановление Правительства РФ от 02.10.2026 N 1288" in text
    # «Связанные новости», боковая колонка, теги, кнопка «Все новости» — не тело
    for junk in ("Связанные новости", "Все новости", "Изменения-2026", "Закон N 44-ФЗ"):
        assert junk not in text, junk


def test_article_parsers_return_empty_string_when_markup_has_no_article_body():
    for domain, parse in ARTICLE_PARSERS.items():
        assert parse("<html><body><nav>Меню</nav><p>Просто страница</p></body></html>") == "", domain


def test_article_parser_ignores_nested_same_tag_noise_inside_skipped_block():
    html = (
        '<section class="article-content"><p>Начало.</p>'
        '<section class="embed-block"><section><span>Читайте также</span></section>Врезка</section>'
        "<p>Конец.</p></section><p>Вне статьи</p>"
    )
    assert ARTICLE_PARSERS["pravo.ru"](html) == "Начало.\nКонец."


@pytest.mark.asyncio
async def test_fetch_article_text_uses_site_parser_for_consultant(monkeypatch):
    body = _fixture("consultant_article_32769.html").encode("utf-8")

    async def fake_fetch(url, **kwargs):
        return FetchResult(url, 200, "text/html; charset=UTF-8", body)

    monkeypatch.setattr(html_source, "fetch_url", fake_fetch)
    text = await fetch_article_text("https://www.consultant.ru/legalnews/32769/", domain="consultant.ru")
    assert text is not None and text.startswith("С 13 октября 2026 года")
    assert "Связанные новости" not in text
