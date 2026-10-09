import asyncio
import uuid
from pathlib import Path

import pytest

from app.providers import html_source, robots
from app.providers.feed_parser import find_feed_links, parse_feed, parse_news_sitemap, strip_tags
from app.providers.generic_html import extract_main_text, parse_generic_listing
from app.providers.html_source import HtmlSourceProvider, SourceHint, fetch_article_text
from app.providers.http_fetch import AccessLimitedError, FetchResult
from app.providers.ssrf import SSRFBlockedError
from app.services import source_onboarding
from app.services.source_onboarding import SourceRejected, discover_source, onboard_source

FIXTURES = Path(__file__).parent / "fixtures"


def _fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_robots_cache():
    robots.clear_cache()
    yield
    robots.clear_cache()


class FakeWeb:
    """Подмена fetch_url: url -> текст | (статус, текст) | исключение. Всё остальное — 404."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.calls: list[str] = []

    async def __call__(self, url, **kwargs):
        self.calls.append(url)
        page = self.pages.get(url)
        if isinstance(page, Exception):
            raise page
        if page is None:
            return FetchResult(url, 404, "text/html", b"")
        status, text = page if isinstance(page, tuple) else (200, page)
        return FetchResult(url, status, "text/html; charset=utf-8", text.encode("utf-8"))


def _patch_web(monkeypatch, pages: dict) -> FakeWeb:
    web = FakeWeb(pages)
    monkeypatch.setattr(source_onboarding, "fetch_url", web)
    monkeypatch.setattr(robots, "fetch_url", web)
    monkeypatch.setattr(html_source, "fetch_url", web)
    return web


# ---------------- RSS / Atom / sitemap ----------------

def test_parse_rss_strips_html_normalizes_date_and_keeps_links():
    items = parse_feed(_fx("generic_rss.xml"), "https://news.example.ru/rss")
    assert [i.title for i in items][:2] == ["Суд признал сделку должника недействительной", "ФНС напомнила о сроках уплаты налога"]
    assert items[0].lead == "Арбитражный суд указал, что сделка совершена с целью вывода активов."
    assert items[0].published == "2026-10-09T10:15:00+03:00"
    assert items[0].url.startswith("https://news.example.ru/2026/10/09/sdelka-dolzhnika")


def test_parse_atom_uses_alternate_link():
    items = parse_feed(_fx("generic_atom.xml"), "https://atom.example.ru/atom")
    assert len(items) == 1
    assert items[0].url == "https://atom.example.ru/news/vs-razyasnil-ubytki"
    assert items[0].published == "2026-10-09T08:30:00Z"


def test_feed_with_doctype_entities_is_rejected_not_expanded():
    assert parse_feed(_fx("generic_evil_feed.xml"), "https://x.example.ru/") == []
    assert parse_feed("это не xml", "https://x.example.ru/") == []
    assert parse_feed("<html><body>страница</body></html>", "https://x.example.ru/") == []


def test_news_sitemap_items_and_index_children():
    items, children = parse_news_sitemap(_fx("generic_news_sitemap.xml"), "https://sm.example.ru/")
    assert [i.title for i in items] == ["Банкротство застройщика: что меняется для дольщиков"]  # <url> без news:title отброшен
    assert items[0].url == "https://sm.example.ru/news/bankrotstvo-zastroyshchika"
    items, children = parse_news_sitemap(_fx("generic_sitemap_index.xml"), "https://sm.example.ru/")
    assert items == [] and children[-1].endswith("sitemap-news-2026.xml")


def test_find_feed_links_and_strip_tags():
    assert find_feed_links(_fx("generic_list.html"), "https://example.ru/news/") == ["https://example.ru/rss/all.xml"]
    assert strip_tags("<p>Один</p><p>Два &laquo;три&raquo;</p>") == "Один Два «три»"


# ---------------- robots.txt ----------------

_ROBOTS = """
User-agent: *
Disallow: /private/
Disallow: /*search/
Allow: /private/public
Disallow: /*.pdf$

User-agent: PravovoyPotokBot
Disallow: /blocked/
"""


def test_robots_own_group_wins_over_star():
    rules = robots.parse_robots(_ROBOTS)
    assert not robots.is_allowed(rules, "/blocked/x")
    assert robots.is_allowed(rules, "/private/x")  # правила «*» к нашему роботу не относятся


def test_robots_star_group_wildcards_longest_match_and_end_anchor():
    text = _ROBOTS.split("User-agent: PravovoyPotokBot")[0]
    rules = robots.parse_robots(text)
    assert not robots.is_allowed(rules, "/private/page")
    assert robots.is_allowed(rules, "/private/public/page")  # Allow длиннее — побеждает
    assert not robots.is_allowed(rules, "/news/search/?q=1")
    assert not robots.is_allowed(rules, "/doc/report.pdf")
    assert robots.is_allowed(rules, "/doc/report.pdf?download=1")  # «$» — только конец адреса
    assert robots.is_allowed(rules, "/news/")


def test_robots_empty_disallow_allows_all_and_disallow_root_blocks_all():
    assert robots.is_allowed(robots.parse_robots("User-agent: *\nDisallow:"), "/anything")
    assert not robots.is_allowed(robots.parse_robots("User-agent: *\nDisallow: /"), "/anything")
    assert robots.is_allowed(robots.parse_robots("User-agent: *\nAllow: /a\nDisallow: /a"), "/a")  # равенство — Allow


@pytest.mark.asyncio
async def test_robots_allows_handles_missing_file_server_error_and_forbidden(monkeypatch):
    _patch_web(monkeypatch, {})  # robots.txt -> 404
    assert await robots.robots_allows("https://a.example.ru/news/")
    robots.clear_cache()
    _patch_web(monkeypatch, {"https://b.example.ru/robots.txt": "User-agent: *\nDisallow: /"})
    assert not await robots.robots_allows("https://b.example.ru/news/")
    robots.clear_cache()
    _patch_web(monkeypatch, {"https://c.example.ru/robots.txt": (503, "")})
    assert not await robots.robots_allows("https://c.example.ru/news/")  # 5xx — считаем запрещённым
    robots.clear_cache()
    _patch_web(monkeypatch, {"https://d.example.ru/robots.txt": AccessLimitedError("403", status_code=403)})
    assert await robots.robots_allows("https://d.example.ru/news/")  # RFC 9309: недоступный robots.txt = без ограничений
    robots.clear_cache()
    _patch_web(monkeypatch, {"https://e.example.ru/robots.txt": AccessLimitedError("timeout")})
    assert not await robots.robots_allows("https://e.example.ru/news/")  # сбой сети — не рискуем


@pytest.mark.asyncio
async def test_robots_is_cached_per_host(monkeypatch):
    web = _patch_web(monkeypatch, {"https://f.example.ru/robots.txt": "User-agent: *\nDisallow: /x/"})
    assert await robots.robots_allows("https://f.example.ru/a")
    assert not await robots.robots_allows("https://f.example.ru/x/b")
    assert web.calls.count("https://f.example.ru/robots.txt") == 1


# ---------------- список статей и тело статьи по эвристикам ----------------

def test_generic_listing_finds_articles_and_skips_menu_tags_external_short_and_files():
    items = parse_generic_listing(_fx("generic_list.html"), "https://example.ru/news/", "example.ru")
    urls = [i.url for i in items]
    assert len(items) == 5
    assert "https://example.ru/news/2026/sud-priznal-sdelku-nedeystvitelnoy" in urls
    assert "https://sub.example.ru/press/12345/" in urls  # поддомен того же сайта
    assert not any("/tag/" in u or "other-site" in u or u.endswith(".pdf") or "/privacy/" in u for u in urls)
    assert items[0].published == "2026-10-09T10:00:00+03:00"  # <time> перед ссылкой
    assert items[2].published == ""


def test_main_text_keeps_article_drops_menu_sidebar_related_comments_footer():
    text = extract_main_text(_fx("generic_article.html"))
    assert "Арбитражный суд признал недействительной сделку по продаже недвижимости" in text
    assert "- имущество возвращено в конкурсную массу." in text
    assert "Решение может быть обжаловано" in text
    for junk in ("Главная", "Популярное", "Самая читаемая", "Поделиться", "Фото: пресс-служба", "Читайте также",
                 "Другая новость", "Комментарий читателя", "Все права защищены", "tracking"):
        assert junk not in text, junk


def test_main_text_returns_empty_when_there_is_no_article_body():
    assert extract_main_text("<html><body><nav>Меню</nav><p>Коротко.</p></body></html>") == ""
    assert extract_main_text("") == ""


# ---------------- подключение сайта ----------------

_PLAIN_HOME = "<html><body><p>Добро пожаловать.</p></body></html>"


@pytest.mark.asyncio
async def test_discover_prefers_rss_found_via_alternate_link(monkeypatch):
    _patch_web(monkeypatch, {
        "https://example.ru/": _fx("generic_list.html"),
        "https://example.ru/rss/all.xml": _fx("generic_rss.xml"),
    })
    result = await discover_source("example.ru")
    assert (result.kind, result.list_url, result.status, result.robots_checked) == ("feed", "https://example.ru/rss/all.xml", "ready", True)


@pytest.mark.asyncio
async def test_discover_falls_back_to_news_sitemap(monkeypatch):
    _patch_web(monkeypatch, {
        "https://example.ru/": _PLAIN_HOME,
        "https://example.ru/news-sitemap.xml": _fx("generic_news_sitemap.xml"),
    })
    result = await discover_source("example.ru")
    assert (result.kind, result.list_url) == ("sitemap", "https://example.ru/news-sitemap.xml")


@pytest.mark.asyncio
async def test_discover_falls_back_to_html_listing_heuristic(monkeypatch):
    home = _fx("generic_list.html").replace('<link rel="alternate" type="application/rss+xml" href="/rss/all.xml">', "")
    _patch_web(monkeypatch, {"https://example.ru/": home})
    result = await discover_source("example.ru")
    assert (result.kind, result.status) == ("html", "ready")
    assert result.list_url == "https://example.ru/"


@pytest.mark.asyncio
async def test_discover_reports_unavailable_when_nothing_found_or_site_down(monkeypatch):
    _patch_web(monkeypatch, {"https://example.ru/": _PLAIN_HOME})
    nothing = await discover_source("example.ru")
    assert (nothing.kind, nothing.status, nothing.robots_checked) == (None, "unavailable", True)
    _patch_web(monkeypatch, {})  # сайт не отвечает (404 на всё)
    down = await discover_source("example.ru")
    assert (down.status, down.robots_checked) == ("unavailable", False)


@pytest.mark.asyncio
async def test_discover_rejects_robots_block_access_block_and_private_addresses(monkeypatch):
    _patch_web(monkeypatch, {"https://example.ru/": _PLAIN_HOME, "https://example.ru/robots.txt": "User-agent: *\nDisallow: /"})
    with pytest.raises(SourceRejected, match="robots.txt"):
        await discover_source("example.ru")
    robots.clear_cache()
    _patch_web(monkeypatch, {"https://example.ru/": AccessLimitedError("blocked", status_code=403)})
    with pytest.raises(SourceRejected, match="ограничивает доступ"):
        await discover_source("example.ru")
    _patch_web(monkeypatch, {"https://example.ru/": SSRFBlockedError("resolves to a non-public address")})
    with pytest.raises(SourceRejected, match="недопустим"):
        await discover_source("example.ru")


@pytest.mark.asyncio
async def test_guessed_path_forbidden_does_not_reject_whole_site(monkeypatch):
    """403 на угаданном адресе ленты — «там нет», а не запрет всему сайту."""
    _patch_web(monkeypatch, {
        "https://example.ru/": _PLAIN_HOME,
        "https://example.ru/rss": AccessLimitedError("blocked", status_code=403),
        "https://example.ru/news-sitemap.xml": _fx("generic_news_sitemap.xml"),
    })
    result = await discover_source("example.ru")
    assert result.kind == "sitemap"


class FakeDb:
    def __init__(self, existing_policy=None):
        self.existing_policy = existing_policy
        self.added = []

    async def scalar(self, *_a, **_k):
        return self.existing_policy

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def rollback(self):
        pass


@pytest.mark.asyncio
async def test_onboard_requires_confirmation_when_no_policy_exists(monkeypatch):
    _patch_web(monkeypatch, {"https://example.ru/": _fx("generic_list.html"), "https://example.ru/rss/all.xml": _fx("generic_rss.xml")})
    with pytest.raises(SourceRejected, match="Подтвердите"):
        await onboard_source(FakeDb(), domain="example.ru", confirmed=False, confirmed_by="u1")


@pytest.mark.asyncio
async def test_onboard_creates_narrow_policy_after_robots_check(monkeypatch):
    _patch_web(monkeypatch, {"https://example.ru/": _fx("generic_list.html"), "https://example.ru/rss/all.xml": _fx("generic_rss.xml")})
    db = FakeDb()
    result = await onboard_source(db, domain="example.ru", confirmed=True, confirmed_by="user-1")
    assert result.kind == "feed"
    (policy,) = db.added
    assert policy.domain == "example.ru"
    assert policy.allowed_actions == ["discover", "fetch", "extract_facts", "temporary_store"]
    assert "retain_full_text" not in policy.allowed_actions and "publish" not in policy.allowed_actions
    assert "user-1" in policy.responsible


@pytest.mark.asyncio
async def test_onboard_does_not_create_policy_if_site_was_unreachable_or_policy_exists(monkeypatch):
    _patch_web(monkeypatch, {})
    db = FakeDb()
    result = await onboard_source(db, domain="example.ru", confirmed=True, confirmed_by="u")
    assert result.status == "unavailable" and db.added == []  # robots.txt не проверен — политику не заводим
    _patch_web(monkeypatch, {"https://example.ru/": _fx("generic_list.html"), "https://example.ru/rss/all.xml": _fx("generic_rss.xml")})
    db2 = FakeDb(existing_policy=uuid.uuid4())
    await onboard_source(db2, domain="example.ru", confirmed=False, confirmed_by="")  # политика уже есть — подтверждение не нужно
    assert db2.added == []


@pytest.mark.asyncio
async def test_builtin_sites_skip_confirmation_and_network(monkeypatch):
    web = _patch_web(monkeypatch, {})
    result = await onboard_source(FakeDb(), domain="garant.ru", confirmed=False, confirmed_by="")
    assert (result.kind, result.status) == ("builtin", "ready") and web.calls == []


# ---------------- провайдер для сайтов с подсказкой ----------------

def _allow(monkeypatch, value=True):
    async def fake(url):
        return value

    monkeypatch.setattr(html_source, "robots_allows", fake)


@pytest.mark.asyncio
async def test_provider_collects_from_feed_hint_strips_utm_and_drops_foreign_links(monkeypatch):
    _patch_web(monkeypatch, {"https://news.example.ru/rss": _fx("generic_rss.xml")})
    _allow(monkeypatch)
    items = await HtmlSourceProvider().discover(
        domains=["example.ru"], theme="", limit=10, sources={"example.ru": SourceHint("feed", "https://news.example.ru/rss")}
    )
    assert [c.title for c in items] == ["Суд признал сделку должника недействительной", "ФНС напомнила о сроках уплаты налога"]
    first = items[0]
    assert first.normalized_url == "https://news.example.ru/2026/10/09/sdelka-dolzhnika"  # utm-метки убраны
    assert first.original_fragment == "Арбитражный суд указал, что сделка совершена с целью вывода активов."
    assert first.story_key.startswith("html:example.ru:") and first.label == "LIVE_LISTING"
    assert first.metadata["published"] == "2026-10-09T10:15:00+03:00"


@pytest.mark.asyncio
async def test_provider_respects_keywords_limit_and_html_kind(monkeypatch):
    _patch_web(monkeypatch, {"https://example.ru/news/": _fx("generic_list.html")})
    _allow(monkeypatch)
    hint = {"example.ru": SourceHint("html", "https://example.ru/news/")}
    p = HtmlSourceProvider()
    banks = await p.discover(domains=["example.ru"], theme="", limit=10, keywords=["банкротстве"], stop_words=None, sources=hint)
    assert [c.title for c in banks] == ["Изменения в закон о банкротстве физических лиц вступают в силу"]
    limited = await p.discover(domains=["example.ru"], theme="", limit=2, sources=hint)
    assert len(limited) == 2


@pytest.mark.asyncio
async def test_provider_skips_site_when_robots_forbids_or_no_hint(monkeypatch):
    web = _patch_web(monkeypatch, {"https://example.ru/rss": _fx("generic_rss.xml")})
    p = HtmlSourceProvider()
    _allow(monkeypatch, False)
    assert await p.discover(domains=["example.ru"], theme="", limit=5, sources={"example.ru": SourceHint("feed", "https://example.ru/rss")}) == []
    assert web.calls == []  # при запрете robots.txt страница даже не скачивалась
    _allow(monkeypatch, True)
    assert await p.discover(domains=["example.ru"], theme="", limit=5, sources={}) == []
    assert await p.discover(domains=["example.ru"], theme="", limit=5, sources={"example.ru": SourceHint("weird", "https://example.ru/")}) == []


@pytest.mark.asyncio
async def test_provider_reads_news_sitemap_hint(monkeypatch):
    _patch_web(monkeypatch, {"https://sm.example.ru/news-sitemap.xml": _fx("generic_news_sitemap.xml")})
    _allow(monkeypatch)
    items = await HtmlSourceProvider().discover(
        domains=["example.ru"], theme="", limit=5, sources={"example.ru": SourceHint("sitemap", "https://sm.example.ru/news-sitemap.xml")}
    )
    assert [c.title for c in items] == ["Банкротство застройщика: что меняется для дольщиков"]


# ---------------- полный текст статьи на неизвестном сайте ----------------

@pytest.mark.asyncio
async def test_article_text_for_generic_site_uses_main_content_extractor(monkeypatch):
    _patch_web(monkeypatch, {"https://news.example.ru/news/a/b": _fx("generic_article.html")})
    _allow(monkeypatch)
    text = await fetch_article_text("https://news.example.ru/news/a/b", domain="example.ru")
    assert text and "Решение может быть обжаловано" in text and "Читайте также" not in text


@pytest.mark.asyncio
async def test_article_text_for_generic_site_respects_robots_and_same_site(monkeypatch):
    web = _patch_web(monkeypatch, {"https://news.example.ru/news/a/b": _fx("generic_article.html"), "https://evil.com/x": _fx("generic_article.html")})
    _allow(monkeypatch, False)
    assert await fetch_article_text("https://news.example.ru/news/a/b", domain="example.ru") is None
    _allow(monkeypatch, True)
    assert await fetch_article_text("https://evil.com/x", domain="example.ru") is None  # чужой сайт
    assert web.calls == []


# ---------------- повторное подключение уже добавленного сайта ----------------

@pytest.mark.asyncio
async def test_changing_to_same_domain_reonboards_legacy_site_only_when_needed(monkeypatch):
    from types import SimpleNamespace

    from app.services import flow_service
    from app.services.source_onboarding import OnboardResult

    calls = []

    async def fake_onboard(db, *, domain, confirmed, confirmed_by):
        calls.append((domain, confirmed))
        return OnboardResult("feed", f"https://{domain}/rss", "ready", robots_checked=True)

    monkeypatch.setattr(flow_service, "onboard_source", fake_onboard)
    legacy = SimpleNamespace(domain="rapsinews.ru", kind=None, list_url=None, status="ready", status_note="", rights_confirmed_by="")
    await flow_service.change_source_domain(None, site=legacy, domain="rapsinews.ru", rights_confirmed=True, confirmed_by="u1")
    assert calls == [("rapsinews.ru", True)]
    assert (legacy.kind, legacy.list_url, legacy.rights_confirmed_by) == ("feed", "https://rapsinews.ru/rss", "u1")

    calls.clear()  # уже подключён и подтверждения нет — повторно не ходим в сеть
    await flow_service.change_source_domain(None, site=legacy, domain="rapsinews.ru")
    assert calls == []

