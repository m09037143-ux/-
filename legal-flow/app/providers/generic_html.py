"""Универсальный разбор HTML для сайтов без собственного парсера.

1. parse_generic_listing — ищет на странице-списке ссылки на статьи (длинный заголовок-
   анкор, путь похож на статью, не меню/подвал) и ближайшую дату <time>.
2. extract_main_text — выделяет тело статьи: берёт контейнер с самым большим объёмом
   «настоящих» абзацев (как в readability), отбрасывая меню, комментарии, врезки и рекламу.

Это эвристики: на нестандартной вёрстке они могут ошибаться, поэтому результат отбора
всё равно проверяет ИИ, а при пустом результате вызывающий код откатывается на анонс."""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app.providers.listing_types import ParsedListItem

_VOID = {"br", "img", "input", "meta", "link", "hr", "source", "area", "base", "col", "embed", "param", "track", "wbr"}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "form", "button", "nav", "footer", "aside", "select", "textarea"}
_NEG = re.compile(
    r"(comment|sidebar|side-bar|footer|menu|nav|breadcrumb|share|social|related|recommend|banner|advert|"
    r"promo|subscribe|cookie|widget|popup|modal|tags?-?list|author-?box|more-news|read-?more)",
    re.IGNORECASE,
)
_POS = re.compile(r"(article|content|post|entry|story|news-?text|text|body|main)", re.IGNORECASE)
_BAD_PATH = re.compile(
    r"/(tag|tags|category|categories|rubric|rubrics|search|page|author|authors|about|contacts?|login|register|cart|"
    r"archive|archives|feed|rss)(/|$)",
    re.IGNORECASE,
)
_BAD_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|docx?|xlsx?|mp[34]|avi|css|js|xml)$", re.IGNORECASE)
_TRACKING = re.compile(r"^(utm_|yclid|gclid|fbclid|from$|ref$)", re.IGNORECASE)


_LAYOUT_TOKEN = re.compile(r"^(has|with|no|is|layout|page|body)[-_]", re.IGNORECASE)


def _classes_ids(attrs: dict) -> str:
    return f"{attrs.get('class') or ''} {attrs.get('id') or ''}"


def _neg(attrs: dict) -> bool:
    """Класс/id говорит «это меню/комментарии/реклама». Служебные токены вёрстки вроде
    «has-sidebar», «page-with-menu» не считаем — иначе мусором окажется вся страница."""
    for token in _classes_ids(attrs).split():
        if _LAYOUT_TOKEN.match(token):
            continue
        if _NEG.search(token):
            return True
    return False


def same_site(host: str, domain: str) -> bool:
    host = host.lower().split(":")[0]
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)


def _looks_like_article_path(path: str) -> bool:
    if path in ("", "/") or _BAD_PATH.search(path) or _BAD_EXT.search(path):
        return False
    segments = [s for s in path.split("/") if s]
    last = segments[-1] if segments else ""
    return len(segments) >= 2 or bool(re.search(r"\d", path)) or ("-" in last and len(last) > 12)


class _ListingParser(HTMLParser):
    def __init__(self, base_url: str, domain: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.domain = domain
        self.items: list[ParsedListItem] = []
        self._seen: set[str] = set()
        self._stack: list[tuple[str, bool]] = []
        self._skip = 0
        self._href: str | None = None
        self._buf: list[str] = []
        self._pending_time = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in _VOID:
            skip = tag in _SKIP_TAGS or _neg(a)
            self._stack.append((tag, skip))
            if skip:
                self._skip += 1
        if self._skip:
            return
        if tag == "time" and a.get("datetime"):
            self._pending_time = a["datetime"]
        elif tag == "a" and a.get("href") and self._href is None:
            self._href = a["href"]
            self._buf = []

    def handle_data(self, data):
        if self._href is not None and not self._skip:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self._finish_anchor()
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                for _t, skipped in self._stack[i:]:
                    if skipped:
                        self._skip -= 1
                del self._stack[i:]
                break

    def _finish_anchor(self) -> None:
        href, text = self._href, " ".join("".join(self._buf).split())
        self._href, self._buf = None, []
        if self._skip or not href:
            return
        words = text.split()
        if len(words) < 4 or not (25 <= len(text) <= 300):
            return
        absolute = urljoin(self.base_url, href.strip())
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or not same_site(parts.netloc, self.domain):
            return
        if not _looks_like_article_path(parts.path):
            return
        url = f"https://{parts.netloc}{parts.path}" + (f"?{parts.query}" if parts.query else "")
        if url in self._seen:
            return
        self._seen.add(url)
        item = ParsedListItem(url=url, title=text, published=self._pending_time)
        self._pending_time = ""
        self.items.append(item)


def parse_generic_listing(html: str, base_url: str, domain: str) -> list[ParsedListItem]:
    parser = _ListingParser(base_url, domain)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser.items


def strip_tracking_params(query: str) -> str:
    kept = [p for p in query.split("&") if p and not _TRACKING.match(p.split("=", 1)[0])]
    return "&".join(kept)


# ---------------- тело статьи ----------------

class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict, parent: "_Node | None") -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list = []
        self.parent = parent


class _DomBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_endtag(self, tag):
        node = self.cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def _is_junk(node: _Node) -> bool:
    if node.tag in ("html", "body", "main", "article"):
        return False
    return node.tag in _SKIP_TAGS or node.tag == "figure" or _neg(node.attrs)


def _text_of(node: _Node) -> str:
    out: list[str] = []

    def walk(n):
        for c in n.children:
            if isinstance(c, str):
                out.append(c)
            elif not _is_junk(c):
                walk(c)

    walk(node)
    return " ".join("".join(out).split())


def _inside_junk(node: _Node) -> bool:
    cur = node.parent
    while cur is not None:
        if _is_junk(cur):
            return True
        cur = cur.parent
    return False


def _all_nodes(node: _Node):
    for c in node.children:
        if isinstance(c, _Node):
            yield c
            yield from _all_nodes(c)


_BLOCK = {"p", "div", "section", "article", "main", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "table", "tr", "br"}


def _emit(node: _Node, lines: list[str], buf: list[str]) -> None:
    def flush(prefix: str = "") -> None:
        text = " ".join("".join(buf).split())
        buf.clear()
        if text:
            lines.append(prefix + text)

    for c in node.children:
        if isinstance(c, str):
            buf.append(c)
            continue
        if _is_junk(c):
            continue
        if c.tag in _BLOCK:
            flush()
            if c.tag == "li":
                _emit(c, lines, buf)
                flush("- ")
            else:
                _emit(c, lines, buf)
                flush()
        else:
            _emit(c, lines, buf)


def extract_main_text(html: str, min_chars: int = 200) -> str:
    """Тело статьи или пустая строка, если подходящего контейнера не нашлось."""
    builder = _DomBuilder()
    try:
        builder.feed(html)
        builder.close()
    except Exception:
        return ""
    scores: dict[int, float] = {}
    nodes: dict[int, _Node] = {}
    for p in _all_nodes(builder.root):
        if p.tag != "p" or _inside_junk(p):
            continue
        text = _text_of(p)
        if len(text) < 40:
            continue
        for level, factor in ((p.parent, 1.0), (p.parent.parent if p.parent else None, 0.5)):
            if level is None or level.tag == "root":
                continue
            scores[id(level)] = scores.get(id(level), 0.0) + len(text) * factor
            nodes[id(level)] = level
    if not scores:
        return ""
    best, best_score = None, 0.0
    for key, base in scores.items():
        node = nodes[key]
        score = base
        if node.tag in ("article", "main") or node.attrs.get("itemprop") == "articleBody":
            score *= 1.5
        elif _POS.search(_classes_ids(node.attrs)):
            score *= 1.2
        if best is None or score > best_score:
            best, best_score = node, score
    if best is None or best_score < min_chars:
        return ""
    lines: list[str] = []
    _emit(best, lines, [])
    return "\n".join(lines)
