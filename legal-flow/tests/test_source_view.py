import uuid
from types import SimpleNamespace

import pytest

from app.models import Discovery
from app.services import ai_service
from app.services.text_similarity import WARN_RUN_WORDS, text_overlap

SOURCE = (
    "Арбитражный суд признал недействительной сделку по продаже недвижимости, совершённую должником за год "
    "до подачи заявления о банкротстве. Суд установил, что имущество было отчуждено по заниженной цене."
)


def test_overlap_is_low_for_a_real_paraphrase_and_high_for_a_copy():
    paraphrase = "Сделка должника с недвижимостью оспорена: имущество продали дёшево незадолго перед банкротством."
    low = text_overlap(paraphrase, SOURCE)
    assert low["share"] < 0.35 and low["warning"] is False
    copy = text_overlap(SOURCE, SOURCE)
    assert copy["share"] == 1.0 and copy["warning"] is True and copy["longest_run_words"] > 20


def test_overlap_warns_on_a_long_verbatim_run_even_with_low_share():
    run = " ".join(f"слово{i}" for i in range(WARN_RUN_WORDS))
    draft = "совсем другое вступление про дело и суд. " + run + " и совсем другая концовка про итоги и выводы редакции " * 5
    result = text_overlap(draft, run)
    assert result["longest_run_words"] >= WARN_RUN_WORDS and result["warning"] is True


def test_overlap_handles_empty_input():
    assert text_overlap("", SOURCE)["share"] == 0.0
    assert text_overlap(SOURCE, "")["share"] == 0.0


class FakeDb:
    def __init__(self, discovery, events=()):
        self.discovery = discovery
        self.events = list(events)

    async def get(self, model, key):
        return self.discovery if model is Discovery else None

    async def scalars(self, *_a, **_k):
        return SimpleNamespace(all=lambda: self.events)


def _news(text="Сделка должника оспорена: имущество продали дёшево незадолго перед банкротством."):
    return SimpleNamespace(
        id=uuid.uuid4(), workspace_id=uuid.uuid4(), discovery_id=uuid.uuid4(), discovery_domain="pravo.ru",
        discovery_original_fragment="Анонс статьи", text=text, source_overlap=None,
    )


def _discovery(label="LIVE_LISTING"):
    return SimpleNamespace(normalized_url="https://pravo.ru/news/1/", metadata_json={"label": label, "published": "2026-10-09"})


@pytest.mark.asyncio
async def test_light_view_does_not_fetch_and_reports_models(monkeypatch):
    news = _news()

    async def boom(url, *, domain):
        raise AssertionError("оригинал не должен загружаться без запроса")

    monkeypatch.setattr(ai_service, "fetch_article_text", boom)
    event = SimpleNamespace(details={"news_id": str(news.id), "models": {"relevance": "yandexgpt-5.1", "facts": "deepseek-v4-flash", "draft": "deepseek-v4-flash"}})
    other = SimpleNamespace(details={"news_id": "другой", "models": {"draft": "x"}})
    view = await ai_service.get_source_view(FakeDb(_discovery(), [other, event]), news, load_text=False)
    assert view["url"] == "https://pravo.ru/news/1/" and view["published"] == "2026-10-09" and view["live"] is True
    assert view["fragment"] == "Анонс статьи" and view["text"] is None
    assert view["models"]["facts"] == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_full_view_loads_original_and_computes_similarity(monkeypatch):
    async def fake_fetch(url, *, domain):
        assert (url, domain) == ("https://pravo.ru/news/1/", "pravo.ru")
        return SOURCE

    monkeypatch.setattr(ai_service, "fetch_article_text", fake_fetch)
    view = await ai_service.get_source_view(FakeDb(_discovery()), _news(), load_text=True)
    assert view["text"] == SOURCE and view["similarity"]["warning"] is False and view["text_note"] is None


@pytest.mark.asyncio
async def test_full_view_without_draft_has_no_similarity_and_failed_fetch_gives_note(monkeypatch):
    async def fake_fetch(url, *, domain):
        return SOURCE

    monkeypatch.setattr(ai_service, "fetch_article_text", fake_fetch)
    view = await ai_service.get_source_view(FakeDb(_discovery()), _news(text=""), load_text=True)
    assert view["text"] == SOURCE and view["similarity"] is None

    async def failing(url, *, domain):
        return None

    monkeypatch.setattr(ai_service, "fetch_article_text", failing)
    view = await ai_service.get_source_view(FakeDb(_discovery()), _news(), load_text=True)
    assert view["text"] is None and "Не удалось загрузить" in view["text_note"]


@pytest.mark.asyncio
async def test_demo_fixture_material_has_no_original(monkeypatch):
    async def boom(url, *, domain):
        raise AssertionError("у демо-материала нет оригинала")

    monkeypatch.setattr(ai_service, "fetch_article_text", boom)
    view = await ai_service.get_source_view(FakeDb(_discovery("DEMO_FIXTURE")), _news(), load_text=True)
    assert view["live"] is False and view["text"] is None and "демонстрационный" in view["text_note"]
