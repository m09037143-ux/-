"""Проверка robots.txt для сайтов, которые пользователь добавил сам.

Собственный разбор, а не urllib.robotparser: тот не понимает «*» и «$» внутри путей
(а ими пользуются почти все сайты: `Disallow: /*search/`). Правила — как у крупных
поисковиков: выбирается группа нашего робота (иначе «*»), побеждает самое длинное
совпадение, при равенстве — Allow. Скачивание — только через fetch_url (SSRF-защита)."""

import logging
import re
import time
from urllib.parse import urlsplit

from app.config import get_settings
from app.providers.http_fetch import AccessLimitedError, fetch_url
from app.providers.ssrf import SSRFBlockedError

logger = logging.getLogger("app.providers.robots")

USER_AGENT_TOKEN = "pravovoypotokbot"  # тот же робот, что в User-Agent у fetch_url
_CACHE_TTL_SECONDS = 3600
_ERROR_TTL_SECONDS = 60
_ROBOTS_MAX_BYTES = 500_000

Rule = tuple[bool, str]  # (allow?, шаблон пути)
_cache: dict[str, tuple[float, list[Rule]]] = {}


def parse_robots(text: str) -> list[Rule]:
    """Возвращает правила группы нашего робота (или «*», если своей группы нет)."""
    groups: list[tuple[list[str], list[Rule]]] = []
    agents: list[str] = []
    rules: list[Rule] = []
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field, value = (p.strip() for p in line.split(":", 1))
        field = field.lower()
        if field == "user-agent":
            if in_rules:
                groups.append((agents, rules))
                agents, rules, in_rules = [], [], False
            agents.append(value.lower())
        elif field in ("allow", "disallow"):
            in_rules = True
            if value:  # пустой Disallow = «можно всё»
                rules.append((field == "allow", value))
            elif field == "disallow":
                rules.append((True, "/"))
    if agents:
        groups.append((agents, rules))

    own = [r for ags, r in groups if any(a != "*" and a in USER_AGENT_TOKEN for a in ags)]
    if own:
        return [rule for group in own for rule in group]
    star = [r for ags, r in groups if "*" in ags]
    return [rule for group in star for rule in group]


def _pattern_to_regex(pattern: str) -> re.Pattern:
    anchored_end = pattern.endswith("$")
    body = pattern[:-1] if anchored_end else pattern
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.compile(regex + ("$" if anchored_end else ""))


def is_allowed(rules: list[Rule], path_and_query: str) -> bool:
    best_len, best_allow = -1, True
    for allow, pattern in rules:
        if _pattern_to_regex(pattern).match(path_and_query):
            if len(pattern) > best_len or (len(pattern) == best_len and allow):
                best_len, best_allow = len(pattern), allow
    return best_allow


async def _load_rules(host: str) -> tuple[list[Rule], int]:
    settings = get_settings()
    try:
        result = await fetch_url(
            f"https://{host}/robots.txt",
            timeout_seconds=settings.fetch_timeout_seconds,
            max_bytes=_ROBOTS_MAX_BYTES,
            max_redirects=settings.fetch_max_redirects,
        )
    except SSRFBlockedError:
        return [(False, "/")], _ERROR_TTL_SECONDS
    except AccessLimitedError as exc:
        if exc.status_code in (401, 403):
            return [], _CACHE_TTL_SECONDS  # robots.txt недоступен для чтения = ограничений нет (RFC 9309)
        logger.warning("robots.txt %s: %s — на этот запуск считаем сбор запрещённым", host, exc)
        return [(False, "/")], _ERROR_TTL_SECONDS
    if result.status_code == 200:
        text = result.body.decode("utf-8", errors="replace")
        return parse_robots(text), _CACHE_TTL_SECONDS
    if 500 <= result.status_code < 600:
        return [(False, "/")], _ERROR_TTL_SECONDS
    return [], _CACHE_TTL_SECONDS  # 404 и прочие 4xx — файла нет, ограничений нет


async def robots_allows(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.netloc
    if not host:
        return False
    cached = _cache.get(host)
    if cached is None or cached[0] < time.monotonic():
        rules, ttl = await _load_rules(host)
        _cache[host] = (time.monotonic() + ttl, rules)
    else:
        rules = cached[1]
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return is_allowed(rules, target)


def clear_cache() -> None:
    _cache.clear()
