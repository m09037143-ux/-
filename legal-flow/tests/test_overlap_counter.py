import uuid
from types import SimpleNamespace

import pytest

from app.models import Discovery
from app.schemas.news import NewsItemOut, NewsListItemOut, SourceOverlapOut
from app.services import ai_service, news_service
from app.services.text_similarity import overlap_record

SOURCE = (
    "Арбитражный суд признал недействительной сделку по продаже недвижимости, совершённую должником за год "
    "до подачи заявления о банкротстве. Суд установил, что имущество было отчуждено по заниженной цене."
)
PARAPHRASE = "Сделка должника с недвижимостью оспорена: имущество продали дёшево незадолго перед банкротством."


class FakeDb:
    def __init__(self, discovery=None):
        self.discovery = discovery
        self.commits = 0

    async def commit(self):
        self.commits += 1

    async def get(self, model, key):
        return self.discovery if model is Discovery else None

    async def scalars(self, *_a, **_k):
        return SimpleNamespace(all=lambda: [])


def test_overlap_record_has_numbers_and_no_source_text():
    rec = overlap_record(PARAPHRASE, SOURCE, "generation")
    assert set(rec) == {"share", "longest_run_words", "warning", "basis", "checked_at", "stale"}
    assert rec["basis"] == "generation" and rec["stale"] is False and rec["warning"] is False
    assert SOURCE[:30] not in str(rec)  # текст оригинала в записи отсутствует
    assert SourceOverlapOut(**rec).share == rec["share"]


def test_copy_of_the_source_is_flagged():
    assert overlap_record(SOURCE, SOURCE, "generation")["warning"] is True


@pytest.mark.asyncio
async def test_mark_overlap_stale_only_when_there_is_a_stored_value():
    db = FakeDb()
    news = SimpleNamespace(source_overlap=overlap_record(PARAPHRASE, SOURCE, "generation"))
    await news_service.mark_overlap_stale(db, news)
    assert news.source_overlap["stale"] is True and db.commits == 1
    await news_service.mark_overlap_stale(db, news)  # уже помечено — лишней записи нет
    assert db.commits == 1
    empty = SimpleNamespace(source_overlap=None)
    await news_service.mark_overlap_stale(db, empty)
    assert empty.source_overlap is None and db.commits == 1


def _patch_generation(monkeypatch, *, source_fragment: str, draft_text: str):
    stmt = SimpleNamespace(statement="факт", status="confirmed", source_fragment="цитата", model_dump=lambda: {"statement": "факт"})

    class Provider:
        async def build_fact_passport(self, db, **kw):
            return SimpleNamespace(statements=[stmt], model_used="m")

        async def draft_independent_text(self, db, **kw):
            return SimpleNamespace(title="Заголовок", text=draft_text, model_used="m")

    async def relevance(db, *, news):
        return SimpleNamespace(is_relevant=True, reasoning="", model_used="m", model_dump=lambda: {})

    async def fragment(db, news):
        return source_fragment

    async def passthrough(db, *, news, **kw):
        return news

    async def to_out(db, news):
        return {}

    async def log(db, **kw):
        return None

    monkeypatch.setattr(ai_service, "_provider", Provider())
    monkeypatch.setattr(ai_service, "_check_relevance_informational", relevance)
    monkeypatch.setattr(ai_service, "_fact_source_fragment", fragment)
    monkeypatch.setattr(ai_service.news_service, "update_facts", passthrough)
    monkeypatch.setattr(ai_service.news_service, "update_draft", passthrough)
    monkeypatch.setattr(ai_service.news_service, "to_internal_out", to_out)
    monkeypatch.setattr(ai_service, "log_activity", log)


def _news():
    return SimpleNamespace(
        id=uuid.uuid4(), workspace_id=uuid.uuid4(), title="Т", version=1, source_overlap=None,
        discovery_original_fragment="Короткий анонс статьи",
    )


@pytest.mark.asyncio
async def test_generation_stores_overlap_when_model_got_the_full_article(monkeypatch):
    _patch_generation(monkeypatch, source_fragment=SOURCE, draft_text=PARAPHRASE)
    news, db = _news(), FakeDb()
    await ai_service.generate_ai_draft(db, news=news, expected_version=1, user_id=uuid.uuid4())
    assert news.source_overlap["basis"] == "generation" and news.source_overlap["warning"] is False
    assert db.commits == 1


@pytest.mark.asyncio
async def test_generation_does_not_store_overlap_when_only_the_announcement_was_used(monkeypatch):
    news = _news()
    _patch_generation(monkeypatch, source_fragment=news.discovery_original_fragment, draft_text=PARAPHRASE)
    await ai_service.generate_ai_draft(FakeDb(), news=news, expected_version=1, user_id=uuid.uuid4())
    assert news.source_overlap is None  # по анониму сравнение бессмысленно


@pytest.mark.asyncio
async def test_generation_flags_a_draft_copied_from_the_source(monkeypatch):
    _patch_generation(monkeypatch, source_fragment=SOURCE, draft_text=SOURCE)
    news = _news()
    await ai_service.generate_ai_draft(FakeDb(), news=news, expected_version=1, user_id=uuid.uuid4())
    assert news.source_overlap["warning"] is True


def _live_discovery():
    return SimpleNamespace(normalized_url="https://pravo.ru/news/1/", metadata_json={"label": "LIVE_LISTING"})


@pytest.mark.asyncio
async def test_source_check_persists_numbers_only_and_plain_view_does_not(monkeypatch):
    async def fake_fetch(url, *, domain):
        return SOURCE

    monkeypatch.setattr(ai_service, "fetch_article_text", fake_fetch)
    news = SimpleNamespace(
        id=uuid.uuid4(), workspace_id=uuid.uuid4(), discovery_id=uuid.uuid4(), discovery_domain="pravo.ru",
        discovery_original_fragment="Анонс", text=PARAPHRASE, source_overlap=None,
    )
    db = FakeDb(_live_discovery())
    await ai_service.get_source_view(db, news, load_text=True)  # просмотр без persist ничего не сохраняет
    assert news.source_overlap is None and db.commits == 0
    view = await ai_service.get_source_view(db, news, load_text=True, persist=True)
    assert news.source_overlap["basis"] == "manual_check" and db.commits == 1
    assert view["overlap"] == news.source_overlap and SOURCE[:30] not in str(news.source_overlap)


def test_api_schemas_accept_stored_overlap():
    rec = overlap_record(PARAPHRASE, SOURCE, "generation")
    item = NewsListItemOut(
        id=uuid.uuid4(), title="Т", discovery_domain="pravo.ru", status="DISCOVERED", has_official_document=False,
        source_overlap=rec, created_at="2026-10-09T10:00:00+00:00",
    )
    assert item.source_overlap.basis == "generation"
    assert "source_overlap" in NewsItemOut.model_fields


def test_list_item_schema_and_latest_job_route_for_auto_refresh():
    """Фоновое обновление страницы опирается на has_draft в списке и на «последнее задание потока»."""
    from app.main import create_app

    item = NewsListItemOut(
        id=uuid.uuid4(), title="Т", discovery_domain="pravo.ru", status="DISCOVERED", has_official_document=False,
        has_draft=True, created_at="2026-10-09T10:00:00+00:00",
    )
    assert item.has_draft is True
    assert "/api/v1/flows/{flow_id}/scan-jobs/latest" in create_app().openapi()["paths"]
