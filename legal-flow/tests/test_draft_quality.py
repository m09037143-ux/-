import uuid
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.errors import AppError
from app.models import Discovery
from app.providers.yandex_gpt import DraftResult, YandexGPTPro51Provider
from app.services import ai_service
from app.services.text_similarity import verbatim_phrases

GOOD = '{"title": "Заголовок", "text": "Самостоятельный текст"}'
BAD_JSON = '{"title": "Заголовок", "text": "в тексте "кавычки" без экранирования"}'
SOURCE = (
    "Арбитражный суд признал недействительной сделку по продаже недвижимости, совершённую должником за год "
    "до подачи заявления о банкротстве. Суд установил, что имущество было отчуждено по заниженной цене."
)
PARAPHRASE = "Сделка должника с недвижимостью оспорена: имущество продали дёшево незадолго перед банкротством."


class FakeLlmDb:
    def __init__(self):
        self.added = []

    async def scalar(self, *_a, **_k):
        return 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


def _provider(monkeypatch, replies):
    settings = Settings(
        llm_provider="yandexgpt_pro_5_1", yandex_folder_id="b1", yandex_api_key="k",
        yandex_model_uri="gpt://b1/yandexgpt-5.1", llm_fixture_mode=False, llm_fallback_model="",
    )
    provider = YandexGPTPro51Provider(settings)
    calls = {"n": 0, "prompts": []}

    async def fake_call(*, prompt, stage):
        calls["prompts"].append(prompt)
        reply = replies[min(calls["n"], len(replies) - 1)]
        calls["n"] += 1
        return reply

    monkeypatch.setattr(provider, "_call_real_model", fake_call)
    return provider, calls


async def _draft(provider, db, **kw):
    return await provider.draft_independent_text(db, workspace_id=uuid.uuid4(), news_item_id=None, title="Т", facts_text="- факт", **kw)


@pytest.mark.asyncio
async def test_invalid_json_is_retried_once_on_the_same_model(monkeypatch):
    provider, calls = _provider(monkeypatch, [BAD_JSON, GOOD])
    db = FakeLlmDb()
    result = await _draft(provider, db)
    assert isinstance(result, DraftResult) and result.text == "Самостоятельный текст"
    assert calls["n"] == 2 and len(db.added) == 2
    assert db.added[0].error.startswith("invalid_json_or_schema") and db.added[1].error == ""


@pytest.mark.asyncio
async def test_invalid_json_twice_gives_the_structure_error(monkeypatch):
    provider, calls = _provider(monkeypatch, [BAD_JSON])
    with pytest.raises(AppError) as exc:
        await _draft(provider, FakeLlmDb())
    assert "не прошёл проверку структуры" in exc.value.message and calls["n"] == 2


@pytest.mark.asyncio
async def test_refusal_is_not_retried(monkeypatch):
    provider, calls = _provider(monkeypatch, ["Я не могу обсуждать эту тему. Давайте поговорим о чём-нибудь ещё."])
    with pytest.raises(AppError):
        await _draft(provider, FakeLlmDb())
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_draft_prompt_demands_independence_and_carries_rewrite_hint(monkeypatch):
    provider, calls = _provider(monkeypatch, [GOOD])
    await _draft(provider, FakeLlmDb(), rewrite_hint="В предыдущем варианте повторялись фразы: «ПОДСКАЗКА».")
    prompt = calls["prompts"][0]
    assert "ни одно предложение не должно повторять формулировки" in prompt and "ПОДСКАЗКА" in prompt
    provider2, calls2 = _provider(monkeypatch, [GOOD])
    await _draft(provider2, FakeLlmDb())
    assert "В предыдущем варианте" not in calls2["prompts"][0]


def test_verbatim_phrases_returns_long_common_runs_longest_first():
    draft = "Вступление. Суд установил что имущество было отчуждено по заниженной цене. Итог и выводы редакции."
    phrases = verbatim_phrases(draft, SOURCE)
    assert phrases and phrases[0].startswith("суд установил что имущество было отчуждено по заниженной цене")
    assert verbatim_phrases(PARAPHRASE, SOURCE) == []


# ---------------- генерация: факты без цитат и переписка при похожести ----------------

class GenDb:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def _patch_gen(monkeypatch, *, source_fragment, drafts, second_raises=False):
    stmt = SimpleNamespace(statement="факт про сделку", status="confirmed", source_fragment="ЦИТАТА ИЗ ИСТОЧНИКА", model_dump=lambda: {})
    seen = {"draft_calls": [], "facts_for_draft": None}

    class Provider:
        async def build_fact_passport(self, db, **kw):
            return SimpleNamespace(statements=[stmt], model_used="m")

        async def draft_independent_text(self, db, **kw):
            seen["draft_calls"].append(kw)
            seen["facts_for_draft"] = kw["facts_text"]
            i = len(seen["draft_calls"]) - 1
            if i >= 1 and second_raises:
                raise AppError("VALIDATION_ERROR", "отказ")
            return SimpleNamespace(title="Т", text=drafts[min(i, len(drafts) - 1)], model_used="m")

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
    return seen


def _news():
    return SimpleNamespace(
        id=uuid.uuid4(), workspace_id=uuid.uuid4(), title="Т", version=1, source_overlap=None,
        discovery_original_fragment="Короткий анонс",
    )


@pytest.mark.asyncio
async def test_draft_gets_facts_without_source_quotes(monkeypatch):
    seen = _patch_gen(monkeypatch, source_fragment=SOURCE, drafts=[PARAPHRASE])
    await ai_service.generate_ai_draft(GenDb(), news=_news(), expected_version=1, user_id=uuid.uuid4())
    assert "факт про сделку" in seen["facts_for_draft"] and "ЦИТАТА" not in seen["facts_for_draft"]
    assert len(seen["draft_calls"]) == 1  # близости к источнику нет — переписка не нужна


@pytest.mark.asyncio
async def test_too_close_draft_is_rewritten_once_and_the_better_version_kept(monkeypatch):
    seen = _patch_gen(monkeypatch, source_fragment=SOURCE, drafts=[SOURCE, PARAPHRASE])
    news = _news()
    await ai_service.generate_ai_draft(GenDb(), news=news, expected_version=1, user_id=uuid.uuid4())
    assert len(seen["draft_calls"]) == 2
    assert "rewrite_hint" not in seen["draft_calls"][0] and "дословно повторялись" in seen["draft_calls"][1]["rewrite_hint"]
    assert news.source_overlap["warning"] is False  # сохранён вариант с меньшим совпадением


@pytest.mark.asyncio
async def test_rewrite_that_is_not_better_or_fails_keeps_the_first_draft(monkeypatch):
    seen = _patch_gen(monkeypatch, source_fragment=SOURCE, drafts=[SOURCE, SOURCE])
    news = _news()
    await ai_service.generate_ai_draft(GenDb(), news=news, expected_version=1, user_id=uuid.uuid4())
    assert len(seen["draft_calls"]) == 2 and news.source_overlap["warning"] is True
    seen2 = _patch_gen(monkeypatch, source_fragment=SOURCE, drafts=[SOURCE], second_raises=True)
    news2 = _news()
    await ai_service.generate_ai_draft(GenDb(), news=news2, expected_version=1, user_id=uuid.uuid4())
    assert len(seen2["draft_calls"]) == 2 and news2.source_overlap["warning"] is True


@pytest.mark.asyncio
async def test_no_rewrite_when_only_the_announcement_was_used(monkeypatch):
    news = _news()
    seen = _patch_gen(monkeypatch, source_fragment=news.discovery_original_fragment, drafts=[SOURCE])
    await ai_service.generate_ai_draft(GenDb(), news=news, expected_version=1, user_id=uuid.uuid4())
    assert len(seen["draft_calls"]) == 1 and news.source_overlap is None


# ---------------- догонка черновиков ----------------

WS = uuid.uuid4()
OWNER = uuid.uuid4()


class CatchUpDb:
    def __init__(self, candidates, events=(), labels=None, owner=OWNER):
        self.results = [list(candidates), list(events)]
        self.labels = labels or {}
        self.owner = owner

    async def scalars(self, *_a, **_k):
        data = self.results.pop(0)
        return SimpleNamespace(all=lambda: data)

    async def get(self, model, key):
        if model is Discovery:
            return SimpleNamespace(metadata_json={"label": self.labels.get(key, "LIVE_LISTING")})
        return None

    async def scalar(self, *_a, **_k):
        return self.owner

    async def rollback(self):
        pass


def _cand(discovery_id=None):
    return SimpleNamespace(id=uuid.uuid4(), workspace_id=WS, discovery_id=discovery_id or uuid.uuid4(), version=1)


def _patch_catch_up(monkeypatch, behaviour=None):
    drafted = []

    async def fake_generate(db, *, news, expected_version, user_id):
        drafted.append((news.id, user_id))
        if behaviour:
            behaviour(news)
        return {}

    async def fake_log(db, **kw):
        return None

    monkeypatch.setattr(ai_service, "generate_ai_draft", fake_generate)
    monkeypatch.setattr(ai_service, "log_activity", fake_log)
    return drafted


@pytest.mark.asyncio
async def test_catch_up_drafts_live_materials_as_owner_and_skips_demo_ones(monkeypatch):
    live, demo = _cand(), _cand()
    drafted = _patch_catch_up(monkeypatch)
    db = CatchUpDb([live, demo], labels={demo.discovery_id: "DEMO_FIXTURE"})
    summary = await ai_service.catch_up_drafts(db)
    assert (summary["generated"], summary["skipped"]) == (1, 1) and drafted == [(live.id, OWNER)]


@pytest.mark.asyncio
async def test_catch_up_gives_up_on_a_material_after_two_failures(monkeypatch):
    stubborn, fresh = _cand(), _cand()
    events = [SimpleNamespace(details={"news_id": str(stubborn.id)}), SimpleNamespace(details={"news_id": str(stubborn.id)})]
    drafted = _patch_catch_up(monkeypatch)
    summary = await ai_service.catch_up_drafts(CatchUpDb([stubborn, fresh], events))
    assert [d[0] for d in drafted] == [fresh.id] and summary["skipped"] == 1


@pytest.mark.asyncio
async def test_catch_up_is_capped_and_stops_on_budget(monkeypatch):
    many = [_cand() for _ in range(8)]
    drafted = _patch_catch_up(monkeypatch)
    summary = await ai_service.catch_up_drafts(CatchUpDb(many), max_items=3)
    assert summary["generated"] == 3 and len(drafted) == 3

    items = [_cand() for _ in range(4)]

    def behaviour(news):
        if news.id == items[1].id:
            raise AppError("LLM_BUDGET_EXCEEDED", "бюджет")

    drafted = _patch_catch_up(monkeypatch, behaviour)
    summary = await ai_service.catch_up_drafts(CatchUpDb(items))
    assert summary["stopped"] == "LLM_BUDGET_EXCEEDED" and len(drafted) == 2


@pytest.mark.asyncio
async def test_catch_up_does_nothing_without_candidates(monkeypatch):
    drafted = _patch_catch_up(monkeypatch)
    summary = await ai_service.catch_up_drafts(CatchUpDb([]))
    assert summary["generated"] == 0 and drafted == []
