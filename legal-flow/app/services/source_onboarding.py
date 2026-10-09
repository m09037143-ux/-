"""Подключение сайта, который пользователь добавил сам.

Порядок: robots.txt → поиск способа сбора (свой парсер → RSS/Atom → новостной sitemap →
список статей по эвристике) → при успехе автоматически заводится source_policy.

Юридическая основа политики — подтверждение пользователя («имею право использовать
материалы сайта») плюс проверка robots.txt. Политика разрешает только discover / fetch /
extract_facts / temporary_store: полный текст не хранится и не публикуется (ТЗ §3).
Все запросы идут через fetch_url (SSRF-защита, HTTPS, лимиты); 401/403/429 не обходятся."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import SourcePolicy
from app.providers.feed_parser import find_feed_links, parse_feed, parse_news_sitemap
from app.providers.generic_html import parse_generic_listing, same_site
from app.providers.html_source import SITES, decode_body
from app.providers.http_fetch import AccessLimitedError, FetchResult, fetch_url
from app.providers.robots import robots_allows
from app.providers.ssrf import SSRFBlockedError

logger = logging.getLogger("app.services.source_onboarding")

POLICY_ACTIONS = ["discover", "fetch", "extract_facts", "temporary_store"]
_FEED_PATHS = [
    "/rss", "/rss/", "/rss.xml", "/feed", "/feed/", "/atom.xml", "/news/rss", "/news/rss/", "/news/feed",
    "/index.xml", "/xml/index.xml", "/rss/news",
]
_SITEMAP_PATHS = ["/news-sitemap.xml", "/sitemap_news.xml", "/sitemap-news.xml", "/sitemap.xml"]
_LIST_PATHS = ["/news/", "/novosti/", "/press/", "/press-center/", "/blog/"]
_MIN_HTML_ITEMS = 5
_DISCOVERY_TIMEOUT_SECONDS = 45


class SourceRejected(Exception):
    """Сайт нельзя подключить (а не «пока недоступен»): запрет в robots.txt, блокировка
    автоматических запросов, недопустимый адрес или не подтверждено право использования."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@dataclass
class OnboardResult:
    kind: str | None
    list_url: str | None
    status: str  # ready | unavailable
    note: str = ""
    robots_checked: bool = False  # robots.txt реально прочитан и сбор не запрещён


async def _get(url: str, *, strict: bool = False) -> FetchResult | None:
    """strict=True (главная страница): 401/403/429 — сайт нельзя подключить. Для угадываемых
    адресов (ленты, sitemap, разделы) такой ответ значит лишь «здесь нет», без отказа сайту."""
    settings = get_settings()
    try:
        result = await fetch_url(
            url,
            timeout_seconds=settings.fetch_timeout_seconds,
            max_bytes=settings.fetch_max_bytes,
            max_redirects=settings.fetch_max_redirects,
        )
    except SSRFBlockedError as exc:
        raise SourceRejected(f"Адрес сайта недопустим: {exc.reason}") from exc
    except AccessLimitedError as exc:
        if strict and exc.status_code in (401, 403, 429):
            raise SourceRejected(
                "Сайт ограничивает доступ автоматическим запросам (HTTP %s) — сбор с него невозможен." % exc.status_code
            ) from exc
        return None
    return result if result.status_code == 200 else None


async def _usable(url: str, domain: str) -> bool:
    return same_site(urlsplit(url).netloc, domain) and await robots_allows(url)


async def discover_source(domain: str) -> OnboardResult:
    """Определяет способ сбора. Не пишет в БД."""
    if domain in SITES:
        return OnboardResult("builtin", SITES[domain][0], "ready", robots_checked=True)

    home = None
    for host in (domain, f"www.{domain}"):
        home = await _get(f"https://{host}/", strict=True)
        if home is not None:
            break
    if home is None:
        return OnboardResult(None, None, "unavailable", "Сайт не отвечает по HTTPS — проверим снова при следующем сборе.")

    base = home.final_url
    if not same_site(urlsplit(base).netloc, domain):
        raise SourceRejected("Сайт перенаправляет на другой домен — добавьте домен, на котором реально находятся новости.")
    if not await robots_allows(base):
        raise SourceRejected("robots.txt сайта запрещает автоматический сбор — добавить его нельзя.")
    text = decode_body(home.body, home.content_type)

    # 1. RSS/Atom: ссылки <link rel="alternate"> и типовые адреса
    feed_urls: list[str] = []
    for u in find_feed_links(text, base) + [urljoin(base, p) for p in _FEED_PATHS]:
        if u not in feed_urls:
            feed_urls.append(u)
    feed_urls = [u for u in feed_urls if same_site(urlsplit(u).netloc, domain)][:12]
    sem = asyncio.Semaphore(6)

    async def probe(u: str):
        async with sem:
            if not await robots_allows(u):
                return None
            r = await _get(u)
            if r is None:
                return None
            return u if parse_feed(decode_body(r.body, r.content_type), r.final_url) else None

    found = await asyncio.gather(*(probe(u) for u in feed_urls), return_exceptions=True)
    for u in found:
        if isinstance(u, SourceRejected):
            raise u
        if isinstance(u, str):
            return OnboardResult("feed", u, "ready", robots_checked=True)

    # 2. Новостной sitemap (Google News) — типовые адреса и вложенные «news»-карты, параллельно
    async def probe_sitemap(path: str):
        async with sem:
            u = urljoin(base, path)
            if not await robots_allows(u):
                return None
            r = await _get(u)
            if r is None:
                return None
            items, children = parse_news_sitemap(decode_body(r.body, r.content_type), r.final_url)
            if items:
                return u
            for child in [c for c in children if "news" in c.lower() and same_site(urlsplit(c).netloc, domain)][:2]:
                if not await robots_allows(child):
                    continue
                cr = await _get(child)
                if cr and parse_news_sitemap(decode_body(cr.body, cr.content_type), cr.final_url)[0]:
                    return child
            return None

    for u in await asyncio.gather(*(probe_sitemap(p) for p in _SITEMAP_PATHS), return_exceptions=True):
        if isinstance(u, SourceRejected):
            raise u
        if isinstance(u, str):
            return OnboardResult("sitemap", u, "ready", robots_checked=True)

    # 3. Список статей на странице по эвристике: главная и типовые разделы новостей
    async def fetch_page(path: str):
        async with sem:
            u = urljoin(base, path)
            if not await robots_allows(u):
                return None
            r = await _get(u)
            return (r.final_url, decode_body(r.body, r.content_type)) if r is not None else None

    pages = [(base, text)]
    for page in await asyncio.gather(*(fetch_page(p) for p in _LIST_PATHS), return_exceptions=True):
        if isinstance(page, SourceRejected):
            raise page
        if isinstance(page, tuple):
            pages.append(page)
    best_url, best_count = None, 0
    for url, html in pages:
        count = len(parse_generic_listing(html, url, domain))
        if count > best_count:
            best_url, best_count = url, count
    if best_url and best_count >= _MIN_HTML_ITEMS:
        return OnboardResult(
            "html", best_url, "ready",
            f"Найден список статей (≈{best_count}); разбор по эвристике, может быть неточным.", robots_checked=True,
        )

    return OnboardResult(
        None, None, "unavailable",
        "Не нашли ни RSS/Atom-ленту, ни новостной sitemap, ни список статей — сбор с этого сайта пока невозможен.",
        robots_checked=True,
    )


async def ensure_policy(db: AsyncSession, *, domain: str, confirmed_by: str) -> None:
    existing = await db.scalar(select(SourcePolicy).where(SourcePolicy.domain == domain, SourcePolicy.material_path == ""))
    if existing is not None:
        return
    db.add(
        SourcePolicy(
            domain=domain,
            material_path="",
            reviewed_at=datetime.now(timezone.utc),
            basis=(
                "Автоматически: robots.txt допускает сбор, право использования материалов сайта подтвердил "
                "пользователь при добавлении. Только анонсы, извлечение фактов и временное хранение; "
                "полный текст не хранится и не публикуется."
            ),
            responsible=f"пользователь {confirmed_by}"[:200],
            allowed_actions=list(POLICY_ACTIONS),
            attribution_required=True,
            attribution_text=f"Источник: {domain}",
        )
    )
    try:
        await db.flush()
    except IntegrityError:  # параллельный запрос успел создать ту же политику
        await db.rollback()


async def onboard_source(db: AsyncSession, *, domain: str, confirmed: bool, confirmed_by: str) -> OnboardResult:
    """Для встроенных сайтов подтверждение не нужно. Для остальных нужно подтверждение
    пользователя, если политики на домен ещё нет."""
    if domain in SITES:
        return OnboardResult("builtin", SITES[domain][0], "ready")

    has_policy = await db.scalar(
        select(SourcePolicy.id).where(SourcePolicy.domain == domain, SourcePolicy.material_path == "").limit(1)
    )
    if not has_policy and not confirmed:
        raise SourceRejected(
            "Подтвердите, что вы вправе использовать материалы этого сайта (галочка в форме добавления), — "
            "без этого сбор с него не включается."
        )
    try:
        result = await asyncio.wait_for(discover_source(domain), timeout=_DISCOVERY_TIMEOUT_SECONDS)
    except (TimeoutError, asyncio.TimeoutError):
        result = OnboardResult(None, None, "unavailable", "Сайт отвечает слишком медленно — проверим снова при следующем сборе.")
    # Политика — только когда robots.txt реально прочитан и не запрещает сбор. Если сайт был
    # недоступен, повторное подключение (с сохранённым подтверждением) пройдёт при сборе.
    if not has_policy and confirmed and result.robots_checked:
        await ensure_policy(db, domain=domain, confirmed_by=confirmed_by)
    return result
