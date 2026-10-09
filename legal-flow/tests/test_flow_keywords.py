from pathlib import Path

import pytest

from app.config import get_settings
from app.providers import html_source
from app.providers.html_source import HtmlSourceProvider, build_candidates, matches_flow, parse_pravo_listing
from app.providers.http_fetch import FetchResult
from app.schemas.flows import CreateFlowRequest, UpdateFlowRequest, clean_word_list
from app.services.scan_service import theme_for_llm
from tests.conftest import run_next_pending_job, unique_email

FIXTURES = Path(__file__).parent / "fixtures"
PRAVO_HTML = (FIXTURES / "pravo_news_list.html").read_text(encoding="utf-8")


# ---------- чистая логика отбора (без БД) ----------

def test_stop_word_excludes_even_when_keyword_matches():
    assert not matches_flow("Банкротство застройщика и санкции", "", "Тема", ["банкротство"], ["санкции"])
    assert matches_flow("Банкротство застройщика", "", "Тема", ["банкротство"], ["санкции"])


def test_keywords_match_by_stem_in_title_or_lead_case_insensitive():
    assert matches_flow("Новая практика ВС", "Дело о банкротстве компании", "Тема", ["Банкротство"], None)
    assert matches_flow("БАНКРОТСТВА станут проще", "", "Тема", ["банкротство"], None)
    assert not matches_flow("Налоги и бухучет", "Про НДС", "Тема", ["банкротство"], None)


def test_multiword_keyword_requires_all_words_any_order():
    assert matches_flow("Взыскание убытков с бывшего директора", "", "Т", ["убытки директора"], None)
    assert not matches_flow("Взыскание долга с директора", "", "Т", ["убытки директора"], None)


def test_without_keywords_theme_is_left_to_the_ai_check():
    # без ключевых слов предфильтр ничего не режет (кроме стоп-слов): тему оценивает ИИ
    assert matches_flow("Запустили роботов-доставщиков", "", "Банкротство и корпоративные споры", [], [])
    assert not matches_flow("Запустили роботов-доставщиков", "", "Тема", [], ["роботов"])


def test_keywords_and_stop_words_match_from_the_start_of_a_word():
    assert matches_flow("Спор о границах участка", "", "Т", ["спор"], None)
    assert not matches_flow("Новые правила для транспорте и логистики", "", "Т", ["спор"], None)  # «спор» внутри слова
    assert matches_flow("Банкротство застройщика", "", "Т", None, ["транспорт"])  # стоп-слово тоже с начала слова
    assert not matches_flow("Новые правила для транспортных компаний", "", "Т", None, ["транспорт"])


def test_interleave_takes_one_per_site_in_turn():
    from app.providers.base import CandidateItem
    from app.services.scan_service import interleave_by_domain

    def c(domain, n):
        return CandidateItem(title=f"{domain}{n}", discovery_domain=domain, normalized_url=f"https://{domain}/{n}", original_fragment="", story_key=f"{domain}{n}")

    ordered = interleave_by_domain([c("a.ru", 1), c("a.ru", 2), c("a.ru", 3), c("b.ru", 1), c("c.ru", 1)])
    assert [x.title for x in ordered] == ["a.ru1", "b.ru1", "c.ru1", "a.ru2", "a.ru3"]


def test_build_candidates_applies_keywords_and_stop_words():
    items = parse_pravo_listing(PRAVO_HTML)
    all_titles = [c.title for c in build_candidates("pravo.ru", items, theme="", limit=10)]
    assert len(all_titles) == 5
    only_housing = build_candidates("pravo.ru", items, theme="", limit=10, keywords=["жилье", "квартира"])
    assert only_housing and all("жиль" in c.title.lower() or "квартир" in c.title.lower() or "жиль" in c.original_fragment.lower() or "квартир" in c.original_fragment.lower() for c in only_housing)
    excluded = build_candidates("pravo.ru", items, theme="", limit=10, stop_words=["газпром"])
    assert all("газпром" not in c.title.lower() and "газпром" not in c.original_fragment.lower() for c in excluded)
    assert len(excluded) < len(all_titles)


def test_clean_word_list_dedupes_trims_and_limits():
    assert clean_word_list(["  Банкротство ", "банкротство", "", "Долг  по   договору"]) == ["Банкротство", "Долг по договору"]
    assert clean_word_list(None) is None
    assert len(clean_word_list([f"слово{i}" for i in range(100)])) == 30
    assert len(clean_word_list(["я" * 500])[0]) == 60


def test_request_schemas_normalize_word_lists():
    req = CreateFlowRequest(name="Н", theme="Т", keywords=[" а ", "А", "б"], stop_words=["", "в"])
    assert req.keywords == ["а", "б"] and req.stop_words == ["в"]
    assert UpdateFlowRequest(keywords=None).keywords is None


class _Flow:
    theme = "Банкротство"
    keywords = ["долг", "убытки"]
    stop_words = ["спорт"]


def test_theme_for_llm_includes_keywords_and_stop_words():
    text = theme_for_llm(_Flow())
    assert text.startswith("Банкротство")
    assert "долг, убытки" in text and "спорт" in text


@pytest.mark.asyncio
async def test_discover_passes_keywords_and_stop_words(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return FetchResult(url, 200, "text/html; charset=UTF-8", PRAVO_HTML.encode("utf-8"))

    monkeypatch.setattr(html_source, "fetch_url", fake_fetch)
    provider = HtmlSourceProvider()
    items = await provider.discover(domains=["pravo.ru"], theme="", limit=10, keywords=["газпром"], stop_words=None)
    assert items and all("газпром" in (c.title + c.original_fragment).lower() for c in items)
    none = await provider.discover(domains=["pravo.ru"], theme="", limit=10, keywords=["такого-слова-нет"], stop_words=None)
    assert none == []


# ---------- через API (нужна тестовая БД) ----------

@pytest.mark.asyncio
async def test_flow_keywords_roundtrip_and_update(authed):
    await authed.post(
        "/api/v1/auth/register",
        json={"name": "N", "email": unique_email("kw"), "password": "correct-horse-battery"},
    )
    created = await authed.post(
        "/api/v1/flows",
        json={"name": "F", "theme": "Т", "domains": ["garant.ru"], "keywords": ["Банкротство", "долг"], "stop_words": ["спорт"]},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["keywords"] == ["Банкротство", "долг"] and body["stop_words"] == ["спорт"]

    patched = await authed.patch(f"/api/v1/flows/{body['id']}", json={"keywords": ["иное"], "stop_words": []})
    assert patched.status_code == 200
    assert patched.json()["keywords"] == ["иное"] and patched.json()["stop_words"] == []
    listed = (await authed.get("/api/v1/flows")).json()
    assert listed[0]["keywords"] == ["иное"]


@pytest.mark.asyncio
async def test_scan_job_uses_flow_keywords_and_stop_words(authed, monkeypatch):
    async def fake_fetch(url, **kwargs):
        return FetchResult(url, 200, "text/html; charset=UTF-8", PRAVO_HTML.encode("utf-8"))

    monkeypatch.setattr(html_source, "fetch_url", fake_fetch)
    monkeypatch.setattr(get_settings(), "source_fixture_mode", False)
    await authed.post(
        "/api/v1/auth/register",
        json={"name": "N", "email": unique_email("kw-scan"), "password": "correct-horse-battery"},
    )
    flow = await authed.post(
        "/api/v1/flows",
        json={"name": "F", "theme": "Т", "domains": ["pravo.ru"], "news_limit_per_run": 10, "keywords": ["газпром"], "stop_words": ["реприватизировать"]},
    )
    flow_id = flow.json()["id"]
    await authed.post(f"/api/v1/flows/{flow_id}/scan-jobs", headers={"Idempotency-Key": "kw-1"})
    job = await run_next_pending_job()
    assert job.status.value == "done"
    titles = [n["title"] for n in (await authed.get("/api/v1/news")).json()]
    assert not any("реприватизировать" in t for t in titles)
