import hashlib
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlsplit

from app.config import get_settings
from app.providers.base import CandidateItem
from app.providers.http_fetch import AccessLimitedError, fetch_url
from app.providers.ssrf import SSRFBlockedError

logger = logging.getLogger("app.providers.html_source")


@dataclass
class ParsedListItem:
    url: str
    title: str
    lead: str = ""
    published: str = ""


class _ListingParser(HTMLParser):
    """Общая обвязка: накапливает поля текущей карточки новости. Конкретная
    разметка сайта описана в подклассах (какие теги/классы начинают карточку,
    заголовок, лид и дату)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._current: dict | None = None
        self._field: str | None = None
        self._field_tag: str | None = None
        self._depth = 0
        self._buf: list[str] = []

    def _start_item(self) -> None:
        self._current = {}
        self.items.append(self._current)

    def _begin(self, field: str, tag: str) -> None:
        if self._current is None or self._current.get(field):
            return
        self._field, self._field_tag, self._depth, self._buf = field, tag, 1, []

    def handle_starttag(self, tag, attrs):
        if self._field is not None and tag == self._field_tag:
            self._depth += 1
        self.start(tag, dict(attrs))

    def handle_endtag(self, tag):
        if self._field is not None and tag == self._field_tag:
            self._depth -= 1
            if self._depth == 0:
                text = " ".join("".join(self._buf).split())
                if self._current is not None and text:
                    self._current[self._field] = text
                self._field = None

    def handle_data(self, data):
        if self._field is not None:
            self._buf.append(data)

    def start(self, tag: str, attrs: dict) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


def _classes(attrs: dict) -> set[str]:
    return set((attrs.get("class") or "").split())


class _GarantParser(_ListingParser):
    """www.garant.ru/news/: карточка = <a class="... stretched-link" href="/news/ID/">заголовок</a>,
    затем <div class="text-secondary">лид</div>, затем блок «small-12» с датой в первом <div>."""

    _HREF = re.compile(r"^/news/\d+/?$")

    def __init__(self) -> None:
        super().__init__()
        self._expect_date = False

    def start(self, tag, attrs):
        classes = _classes(attrs)
        if tag == "a" and "stretched-link" in classes and self._HREF.match(attrs.get("href") or ""):
            self._start_item()
            self._current["url"] = attrs["href"]
            self._expect_date = False
            self._begin("title", "a")
        elif tag == "div" and self._current is not None:
            if classes == {"text-secondary"}:
                self._begin("lead", "div")
            elif "small-12" in classes:
                self._expect_date = True
            elif self._expect_date:
                self._expect_date = False
                self._begin("published", "div")


class _PravoParser(_ListingParser):
    """pravo.ru/news/: карточка = <div class="section-article__listing-item"> с <time datetime>,
    ссылкой section-article__listing-link, <header> (заголовок) и <article> (лид)."""

    def start(self, tag, attrs):
        classes = _classes(attrs)
        if tag == "div" and "section-article__listing-item" in classes:
            self._start_item()
        elif self._current is None:
            return
        elif tag == "time" and attrs.get("datetime") and not self._current.get("published"):
            self._current["published"] = attrs["datetime"]
        elif tag == "a" and "section-article__listing-link" in classes and not self._current.get("url"):
            self._current["url"] = attrs.get("href") or ""
        elif tag == "header" and self._current.get("url"):
            self._begin("title", "header")
        elif tag == "article" and self._current.get("url"):
            self._begin("lead", "article")


def _parse(parser: _ListingParser, html: str, base_url: str) -> list[ParsedListItem]:
    parser.feed(html)
    parser.close()
    result = []
    for raw in parser.items:
        if not raw.get("url") or not raw.get("title"):
            continue
        result.append(
            ParsedListItem(
                url=urljoin(base_url, raw["url"]),
                title=raw["title"],
                lead=raw.get("lead", ""),
                published=raw.get("published", ""),
            )
        )
    return result


def parse_garant_listing(html: str, base_url: str = "https://www.garant.ru/news/") -> list[ParsedListItem]:
    return _parse(_GarantParser(), html, base_url)


def parse_pravo_listing(html: str, base_url: str = "https://pravo.ru/news/") -> list[ParsedListItem]:
    return _parse(_PravoParser(), html, base_url)


# list_url для pravo.ru — без «www»: у www.pravo.ru не проходит проверка TLS-сертификата,
# а fetch_url проверку не отключает (и не должен).
SITES = {
    "garant.ru": ("https://www.garant.ru/news/", parse_garant_listing),
    "pravo.ru": ("https://pravo.ru/news/", parse_pravo_listing),
}
SUPPORTED_DOMAINS = frozenset(SITES)

_ID_RE = re.compile(r"/news/(\d+)")


def theme_keywords(theme: str) -> list[str]:
    """Грубые «основы» слов темы: слова от 4 букв, у длинных отрезаем 2 последние буквы,
    чтобы «банкротство» находило «банкротстве». Точную релевантность потом проверяет
    check_relevance (LLM) — здесь только дешёвый предфильтр."""
    words = re.findall(r"[a-zа-яё0-9]+", theme.lower())
    return [w[: max(4, len(w) - 2)] for w in words if len(w) >= 4]


def matches_theme(title: str, theme: str) -> bool:
    keywords = theme_keywords(theme)
    if not keywords:
        return True
    low = title.lower()
    return any(k in low for k in keywords)


def decode_body(body: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if not m:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.I)
        charset = m.group(1).decode("ascii") if m else "utf-8"
    else:
        charset = m.group(1)
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def build_candidates(domain: str, items: list[ParsedListItem], theme: str, limit: int) -> list[CandidateItem]:
    candidates: list[CandidateItem] = []
    for item in items:
        if len(candidates) >= limit:
            break
        if not matches_theme(item.title, theme):
            continue
        parts = urlsplit(item.url)
        url = f"{parts.scheme}://{parts.netloc}{parts.path}"
        id_match = _ID_RE.search(parts.path)
        story_key = f"html:{domain}:{id_match.group(1) if id_match else parts.path}"
        fragment = item.lead or item.title
        candidates.append(
            CandidateItem(
                title=item.title,
                discovery_domain=domain,
                normalized_url=url,
                original_fragment=fragment,
                story_key=story_key,
                content_hash=hashlib.sha256(f"{item.title}\n{item.lead}".encode("utf-8")).hexdigest(),
                label="LIVE_LISTING",
                metadata={"published": item.published},
            )
        )
    return candidates


class HtmlSourceProvider:
    """Реальный сбор со страниц списка новостей поддерживаемых сайтов. Скачивает
    только через fetch_url (SSRF-защита, HTTPS, лимиты). Берёт из списка лишь
    заголовок, ссылку, дату и короткий лид — полный текст статей не скачивается
    и не хранится (policy: нет retain_full_text). При 401/403/429 и сетевых сбоях
    домен пропускается без повторов и обхода."""

    name = "html"

    async def discover(self, *, domains: list[str], theme: str, limit: int) -> list[CandidateItem]:
        settings = get_settings()
        found: list[CandidateItem] = []
        for domain in domains:
            site = SITES.get(domain)
            if site is None:
                continue
            list_url, parse = site
            try:
                result = await fetch_url(
                    list_url,
                    timeout_seconds=settings.fetch_timeout_seconds,
                    max_bytes=settings.fetch_max_bytes,
                    max_redirects=settings.fetch_max_redirects,
                )
            except (AccessLimitedError, SSRFBlockedError) as exc:
                logger.warning("html source %s skipped: %s", domain, exc)
                continue
            if result.status_code != 200:
                logger.warning("html source %s skipped: HTTP %s", domain, result.status_code)
                continue
            items = parse(decode_body(result.body, result.content_type), result.final_url)
            if not items:
                logger.warning("html source %s: no news items parsed — markup may have changed", domain)
            found.extend(build_candidates(domain, items, theme, limit))
        return found


class _ArticleTextParser(HTMLParser):
    """Общий, не специфичный для сайта запасной разбор: вытаскивает видимый текст
    страницы, пропуская <script>/<style>/<nav>/<header>/<footer>/<aside> и вставляя
    перенос строки на блочных тегах. Включает текст меню/подвала вперемешку со
    статьёй — используется, пока для домена не заведён точный разбор в
    ARTICLE_PARSERS (нужна живая разметка сайта, которой нет в этой среде
    разработки — см. docs/ROADMAP.md)."""

    _SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript"}
    _BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def extract_visible_text(html: str) -> str:
    parser = _ArticleTextParser()
    parser.feed(html)
    parser.close()
    return parser.text()


# Точный разбор тела статьи (без меню/подвала/рекламы) по доменам — заполняется по
# мере изучения живой разметки каждого сайта. Домен без записи здесь получает
# extract_visible_text() как грубый запасной вариант (см. его docstring).
ARTICLE_PARSERS: dict[str, Callable[[str], str]] = {}

_ARTICLE_TEXT_MAX_CHARS = 12_000


async def fetch_article_text(url: str, *, domain: str) -> str | None:
    """Скачивает страницу статьи и возвращает её текст — ТОЛЬКО для разового
    использования как вход LLM-этапа (build_fact_passport): source_policies
    разрешают fetch/extract_facts/temporary_store, но не retain_full_text —
    вызывающий код не должен сохранять результат в БД как есть, только
    извлечённые из него факты. Возвращает None при любой ошибке или блокировке;
    вызывающий код обязан в этом случае откатиться на уже сохранённый короткий
    фрагмент (discovery_original_fragment), а не падать."""
    settings = get_settings()
    try:
        result = await fetch_url(
            url,
            timeout_seconds=settings.fetch_timeout_seconds,
            max_bytes=settings.fetch_max_bytes,
            max_redirects=settings.fetch_max_redirects,
        )
    except (AccessLimitedError, SSRFBlockedError) as exc:
        logger.warning("article fetch %s skipped: %s", url, exc)
        return None
    if result.status_code != 200:
        logger.warning("article fetch %s skipped: HTTP %s", url, result.status_code)
        return None
    parse = ARTICLE_PARSERS.get(domain, extract_visible_text)
    text = parse(decode_body(result.body, result.content_type)).strip()
    return text[:_ARTICLE_TEXT_MAX_CHARS] if text else None
