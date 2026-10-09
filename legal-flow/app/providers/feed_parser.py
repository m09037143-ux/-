"""Разбор RSS 2.0 / RSS 1.0 (RDF) / Atom и новостного sitemap (Google News) в список записей.

XML с DOCTYPE/ENTITY отклоняется целиком (защита от «взрыва сущностей»): настоящим
лентам они не нужны. Текст описаний очищается от HTML-тегов."""

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

from app.providers.listing_types import ParsedListItem

_XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)
_FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/rdf+xml")


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4"):
            self.parts.append(" ")

    def handle_data(self, data):
        self.parts.append(data)


def strip_tags(fragment: str, limit: int = 500) -> str:
    if not fragment:
        return ""
    stripper = _Stripper()
    try:
        stripper.feed(fragment)
        stripper.close()
    except Exception:  # битая разметка в описании — берём как есть
        return " ".join(fragment.split())[:limit]
    return " ".join("".join(stripper.parts).split())[:limit]


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _safe_root(text: str) -> ET.Element | None:
    upper = text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        return None
    try:
        return ET.fromstring(_XML_DECL.sub("", text, count=1).strip())
    except ET.ParseError:
        return None


def _child_text(el: ET.Element, name: str) -> str:
    for child in el:
        if _local(child.tag) == name and (child.text or "").strip():
            return child.text.strip()
    return ""


def _normalize_date(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return raw


def parse_feed(text: str, base_url: str) -> list[ParsedListItem]:
    root = _safe_root(text)
    if root is None or _local(root.tag) not in ("rss", "feed", "RDF"):
        return []
    items: list[ParsedListItem] = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "item":
            title = _child_text(el, "title")
            link = _child_text(el, "link")
            if not link:
                guid = _child_text(el, "guid")
                link = guid if guid.startswith("http") else ""
            lead = _child_text(el, "description") or _child_text(el, "encoded")
            published = _child_text(el, "pubDate") or _child_text(el, "date")
        elif name == "entry":
            title = _child_text(el, "title")
            link = ""
            for child in el:
                if _local(child.tag) == "link" and child.get("href") and child.get("rel", "alternate") == "alternate":
                    link = child.get("href")
                    break
            lead = _child_text(el, "summary") or _child_text(el, "content")
            published = _child_text(el, "published") or _child_text(el, "updated")
        else:
            continue
        title = strip_tags(title, 300)
        if not title or not link:
            continue
        items.append(
            ParsedListItem(
                url=urljoin(base_url, link.strip()),
                title=title,
                lead=strip_tags(lead, 500),
                published=_normalize_date(published),
            )
        )
    return items


def parse_news_sitemap(text: str, base_url: str) -> tuple[list[ParsedListItem], list[str]]:
    """(записи, адреса вложенных sitemap). Записи берутся только если у <url> есть
    <news:title> — обычный sitemap без заголовков новостей нам ничего не даёт."""
    root = _safe_root(text)
    if root is None:
        return [], []
    items: list[ParsedListItem] = []
    children: list[str] = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "url":
            loc = _child_text(el, "loc")
            news = next((c for c in el if _local(c.tag) == "news"), None)
            if loc and news is not None:
                title = strip_tags(_child_text(news, "title"), 300)
                if title:
                    items.append(
                        ParsedListItem(
                            url=urljoin(base_url, loc),
                            title=title,
                            published=_normalize_date(_child_text(news, "publication_date")),
                        )
                    )
        elif name == "sitemap":
            loc = _child_text(el, "loc")
            if loc:
                children.append(urljoin(base_url, loc))
    return items, children


class _AlternateFinder(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = dict(attrs)
        if "alternate" in (a.get("rel") or "").lower() and (a.get("type") or "").lower() in _FEED_TYPES and a.get("href"):
            self.urls.append(urljoin(self.base_url, a["href"]))


def find_feed_links(html: str, base_url: str) -> list[str]:
    finder = _AlternateFinder(base_url)
    try:
        finder.feed(html)
        finder.close()
    except Exception:
        pass
    return finder.urls
