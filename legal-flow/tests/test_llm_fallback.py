import uuid

import pytest

from app.config import Settings
from app.errors import AppError
from app.providers import yandex_gpt
from app.providers.llm_targets import AiConfig
from app.providers.yandex_gpt import DraftResult, YandexGPTPro51Provider

REFUSAL = "Я не могу обсуждать эту тему. Давайте поговорим о чём-нибудь ещё."
GOOD_JSON = '{"title": "Заголовок", "text": "Текст материала"}'
FOLDER = "b1gtest"


class FakeDb:
    """Только то, что трогает _run_stage: бюджет (scalar), add и commit — БД не нужна."""

    def __init__(self):
        self.added = []

    async def scalar(self, *_args, **_kwargs):
        return 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


def _provider(monkeypatch, *, primary, fallback_reply=None, fallback_model="deepseek-v4-flash"):
    settings = Settings(
        llm_provider="yandexgpt_pro_5_1",
        yandex_folder_id=FOLDER,
        yandex_api_key="test-key",
        yandex_model_uri=f"gpt://{FOLDER}/yandexgpt-5.1",
        llm_fixture_mode=False,
        llm_fallback_model=fallback_model,
    )
    provider = YandexGPTPro51Provider(settings)
    calls = {"primary": 0, "fallback": 0}

    async def fake_call(target, *, prompt, stage):
        if target.kind == "yandex_native":  # основная модель из настроек сервера
            calls["primary"] += 1
            return primary
        calls["fallback"] += 1  # запасная — через OpenAI-совместимый эндпоинт Яндекса
        assert target.model == "deepseek-v4-flash"
        return fallback_reply

    async def no_admin_config(**kwargs):
        return AiConfig()  # в админке ничего не задано — работают настройки сервера

    monkeypatch.setattr(provider, "_call_target", fake_call)
    monkeypatch.setattr(yandex_gpt, "load_ai_config", no_admin_config)
    return provider, calls


async def _run(provider, db):
    return await provider._run_stage(
        db, workspace_id=uuid.uuid4(), news_item_id=None, stage="draft", prompt="p",
        result_model=DraftResult, fixture_response={"title": "t", "text": "x"},
    )


@pytest.mark.asyncio
async def test_primary_ok_never_calls_fallback(monkeypatch):
    provider, calls = _provider(monkeypatch, primary=GOOD_JSON, fallback_reply=GOOD_JSON)
    db = FakeDb()
    result = await _run(provider, db)
    assert result.model_used == "yandexgpt-5.1"
    assert calls == {"primary": 1, "fallback": 0}
    assert [c.model_uri for c in db.added] == [f"gpt://{FOLDER}/yandexgpt-5.1"]


@pytest.mark.asyncio
async def test_refusal_switches_to_fallback_and_logs_both_calls(monkeypatch):
    provider, calls = _provider(monkeypatch, primary=REFUSAL, fallback_reply=GOOD_JSON)
    db = FakeDb()
    result = await _run(provider, db)
    assert result.text == "Текст материала"
    assert result.model_used == "deepseek-v4-flash"
    assert calls == {"primary": 1, "fallback": 1}
    assert [c.model_uri for c in db.added] == [
        f"gpt://{FOLDER}/yandexgpt-5.1",
        f"gpt://{FOLDER}/deepseek-v4-flash",
    ]
    assert db.added[0].error.startswith("model_refused")
    assert db.added[1].error == ""
    # model_used — служебная пометка, в ответы API (model_dump) не попадает
    assert "model_used" not in result.model_dump()


@pytest.mark.asyncio
async def test_refusal_without_fallback_gives_friendly_error(monkeypatch):
    provider, calls = _provider(monkeypatch, primary=REFUSAL, fallback_model="")
    with pytest.raises(AppError) as exc:
        await _run(provider, FakeDb())
    assert "отказался обрабатывать" in exc.value.message
    assert calls["fallback"] == 0


@pytest.mark.asyncio
async def test_both_models_refuse(monkeypatch):
    provider, calls = _provider(monkeypatch, primary=REFUSAL, fallback_reply=REFUSAL)
    with pytest.raises(AppError) as exc:
        await _run(provider, FakeDb())
    assert "отказался обрабатывать" in exc.value.message
    assert calls == {"primary": 1, "fallback": 1}


@pytest.mark.asyncio
async def test_invalid_json_twice_from_primary_switches_to_fallback(monkeypatch):
    provider, calls = _provider(monkeypatch, primary="это не JSON", fallback_reply=GOOD_JSON)
    db = FakeDb()
    result = await _run(provider, db)
    assert result.model_used == "deepseek-v4-flash"
    assert calls == {"primary": 2, "fallback": 1}  # повтор на основной, затем запасная модель
    assert [c.model_uri.rsplit("/", 1)[-1] for c in db.added] == ["yandexgpt-5.1", "yandexgpt-5.1", "deepseek-v4-flash"]


@pytest.mark.asyncio
async def test_invalid_json_without_fallback_model_is_a_structure_error(monkeypatch):
    provider, calls = _provider(monkeypatch, primary="это не JSON", fallback_model="")
    with pytest.raises(AppError) as exc:
        await _run(provider, FakeDb())
    assert "не прошёл проверку структуры" in exc.value.message
    assert calls == {"primary": 2, "fallback": 0}


@pytest.mark.asyncio
async def test_invalid_json_from_both_models_is_a_structure_error(monkeypatch):
    provider, calls = _provider(monkeypatch, primary="это не JSON", fallback_reply="тоже не JSON")
    with pytest.raises(AppError) as exc:
        await _run(provider, FakeDb())
    assert "не прошёл проверку структуры" in exc.value.message
    assert calls == {"primary": 2, "fallback": 2}


@pytest.mark.asyncio
async def test_unknown_fallback_model_is_a_config_error_not_a_silent_swap(monkeypatch):
    provider, calls = _provider(monkeypatch, primary=GOOD_JSON, fallback_model="some-other-model")
    with pytest.raises(AppError) as exc:
        await _run(provider, FakeDb())
    assert exc.value.code == "LLM_NOT_CONFIGURED"
    assert calls == {"primary": 0, "fallback": 0}
