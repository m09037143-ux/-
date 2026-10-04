from pathlib import Path

import pytest

from app.config import get_settings
from app.providers import html_source
from app.providers.html_source import (
    HtmlSourceProvider,
    build_candidates,
    decode_body,
    matches_theme,
    parse_garant_listing,
    parse_pravo_listing,
)
from app.providers.http_fetch import AccessLimitedError, FetchResult
from tests.conftest import run_next_pending_job, unique_email

FIXTURES = Path(__file__).parent / "fixtures"
# Сохранённые куски реальной разметки списков новостей (октябрь 2026): тесты не ходят в сеть
# и не ломаются от изменений сайтов, но при смене разметки парсер нужно обновить вместе с ними.
GARANT_HTML = (FIXTURES / "garant_news_list.html").read_text(encoding="utf-8")
PRAVO_HTML = (FIXTURES / "pravo_news_list.html").read_text(encoding="utf-8")


def test_parse_garant_listing_extracts_title_url_lead_date():
    items = parse_garant_listing(GARANT_HTML)
    assert len(items) == 5
    first = items[0]
    assert first.title == "В 34 регионах России запустили эксперимент по использованию роботов-доставщиков"
    assert first.url == "https://www.garant.ru/news/2261851/"
    assert first.lead.startswith("Они могут работать на тротуарах")
    assert "2 октября 2026" in first.published
    assert all(i.url.startswith("https://www.garant.ru/news/") and i.title and i.lead for i in items)


def test_parse_pravo_listing_extracts_title_url_lead_date():
    items = parse_pravo_listing(PRAVO_HTML)
    assert len(items) == 5
    first = items[0]
    assert first.title == "Немецкие власти решили реприватизировать бывшую «дочку» «Газпрома»"
    assert first.url == "https://pravo.ru/news/266117/"
    assert first.lead.startswith("Стоимость SEFE")
    assert first.published == "2026-10-02T19:04:05Z"


def test_parsers_return_nothing_on_unrelated_markup():
    assert parse_garant_listing("<html><body><p>нет новостей</p></body></html>") == []
    assert parse_pravo_listing("<html><body><header>Меню</header></body></html>") == []


def test_matches_theme_is_case_insensitive_stem_match():
    assert matches_theme("Банкротство застройщика: новое разъяснение ВС", "Банкротство и корпоративные споры")
    assert matches_theme("ВС РФ рассмотрел КОРПОРАТИВНЫЙ спор", "корпоративные споры")
    assert not matches_theme("Запустили роботов-доставщиков", "Банкротство и корпоративные споры")
    # тема без слов от 4 букв — предфильтр не применяется
    assert matches_theme("Любой заголовок", "и в на")


def test_decode_body_handles_windows_1251():
    body = "Новости".encode("cp1251")
    assert decode_body(body, "text/html; charset=windows-1251") == "Новости"
    meta = b'<meta charset="windows-1251">' + body
    assert decode_body(meta, "text/html").endswith("Новости")
    assert decode_body("Новости".encode("utf-8"), "text/html; charset=UTF-8") == "Новости"


def test_build_candidates_respects_limit_and_normalizes():
    items = parse_pravo_listing(PRAVO_HTML)
    candidates = build_candidates("pravo.ru", items, theme="", limit=2)
    assert len(candidates) == 2
    c = candidates[0]
    assert c.discovery_domain == "pravo.ru"
    assert c.normalized_url == "https://pravo.ru/news/266117"[: len("https://pravo.ru/news/266117")] + "/"
    assert c.story_key == "html:pravo.ru:266117"
    assert c.label == "LIVE_LISTING"
    assert c.content_hash and len(c.content_hash) == 64


def _fake_fetch(pages: dict[str, FetchResult | Exception]):
    async def fetch(url, **kwargs):
        result = pages[url]
        if isinstance(result, Exception):
            raise result
        return result

    return fetch


@pytest.mark.asyncio
async def test_discover_uses_fetch_url_and_filters_by_theme(monkeypatch):
    pages = {
        "https://www.garant.ru/news/": FetchResult(
            "https://www.garant.ru/news/", 200, "text/html; charset=windows-1251", GARANT_HTML.encode("cp1251", errors="replace")
        ),
        "https://pravo.ru/news/": FetchResult("https://pravo.ru/news/", 200, "text/html; charset=UTF-8", PRAVO_HTML.encode("utf-8")),
    }
    monkeypatch.setattr(html_source, "fetch_url", _fake_fetch(pages))

    provider = HtmlSourceProvider()
    all_items = await provider.discover(domains=["garant.ru", "pravo.ru"], theme="", limit=3)
    assert {c.discovery_domain for c in all_items} == {"garant.ru", "pravo.ru"}
    assert len(all_items) == 6  # limit — на домен
    assert any(c.title.startswith("В 34 регионах") for c in all_items)

    filtered = await provider.discover(domains=["garant.ru", "pravo.ru"], theme="жилье квартира", limit=5)
    assert filtered and all("жиль" in c.title.lower() or "квартир" in c.title.lower() for c in filtered)


@pytest.mark.asyncio
async def test_discover_skips_domain_on_access_limited_and_ignores_unsupported(monkeypatch):
    pages = {
        "https://www.garant.ru/news/": AccessLimitedError("blocked", status_code=403),
        "https://pravo.ru/news/": FetchResult("https://pravo.ru/news/", 200, "text/html; charset=UTF-8", PRAVO_HTML.encode("utf-8")),
    }
    monkeypatch.setattr(html_source, "fetch_url", _fake_fetch(pages))
    items = await HtmlSourceProvider().discover(domains=["garant.ru", "pravo.ru", "example.com"], theme="", limit=2)
    assert {c.discovery_domain for c in items} == {"pravo.ru"}


async def _create_flow(authed, prefix: str, domains: list[str]) -> str:
    await authed.post(
        "/api/v1/auth/register",
        json={"name": "N", "email": unique_email(prefix), "password": "correct-horse-battery"},
    )
    flow = await authed.post(
        "/api/v1/flows",
        json={"name": "F", "theme": "Т", "domains": domains, "news_limit_per_run": 3},
    )
    assert flow.status_code == 201, flow.text
    return flow.json()["id"]


@pytest.mark.asyncio
async def test_scan_job_uses_html_provider_for_supported_domains(authed, monkeypatch):
    pages = {
        "https://www.garant.ru/news/": FetchResult(
            "https://www.garant.ru/news/", 200, "text/html; charset=windows-1251", GARANT_HTML.encode("cp1251", errors="replace")
        ),
        "https://pravo.ru/news/": FetchResult("https://pravo.ru/news/", 200, "text/html; charset=UTF-8", PRAVO_HTML.encode("utf-8")),
    }
    monkeypatch.setattr(html_source, "fetch_url", _fake_fetch(pages))
    # SOURCE_FIXTURE_MODE defaults to true for the whole test suite (same reasoning as
    # LLM_FIXTURE_MODE) — flip it off just for this test so provider_name_for_flow
    # actually dispatches to "html" instead of staying on "fixture".
    settings = get_settings()
    monkeypatch.setattr(settings, "source_fixture_mode", False)
    flow_id = await _create_flow(authed, "html-scan", ["garant.ru", "pravo.ru"])

    resp = await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "html-1"})
    assert resp.status_code == 202
    assert resp.json()["provider_name"] == "html"

    job = await run_next_pending_job()
    assert job.status.value == "done"
    assert len(job.created_news_ids) == 6

    titles = [n["title"] for n in (await authed.get("/api/v1/news")).json()]
    assert "Немецкие власти решили реприватизировать бывшую «дочку» «Газпрома»" in titles
    assert not any("Ответственность руководителя" in t for t in titles)


@pytest.mark.asyncio
async def test_scan_job_falls_back_to_fixture_for_unsupported_domain(authed, monkeypatch):
    monkeypatch.setattr(get_settings(), "source_fixture_mode", False)
    flow_id = await _create_flow(authed, "fixture-fallback", ["garant.ru", "example.com"])
    resp = await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "fx-1"})
    assert resp.json()["provider_name"] == "fixture"
    # Left pending otherwise — run_next_pending_job() polls the whole table, not just
    # this flow, so a leftover row here would steal a later test's job (see the same
    # caveat documented in tests/test_worker_polling.py).
    await run_next_pending_job()
