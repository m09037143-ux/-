import uuid
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from app import secrets_box
from app.config import Settings, get_settings
from app.errors import AppError
from app.models import AiConnection, AiStageModel
from app.providers import llm_targets, yandex_gpt
from app.providers.llm_targets import AiConfig, ModelTarget, build_target, call_model, validate_base_url
from app.providers.yandex_gpt import DraftResult, FactPassportResult, YandexGPTPro51Provider
from app.schemas.admin_ai import ConnectionCreate, ConnectionUpdate, StageIn, StagesUpdate
from app.services import ai_admin_service

SECRET = "sk-test-SECRET-1234567890abcd"
GOOD_DRAFT = '{"title": "Заголовок", "text": "Текст материала"}'


@pytest.fixture
def key(monkeypatch):
    """Рабочий ключ шифрования на время теста (подставляется в живые настройки процесса)."""
    value = Fernet.generate_key().decode()
    monkeypatch.setattr(get_settings(), "secrets_encryption_key", value)
    llm_targets.invalidate_cache()
    yield value
    llm_targets.invalidate_cache()


# ---------------- шифрование ----------------

def test_secrets_roundtrip_and_ciphertext_does_not_contain_the_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "secrets_encryption_key", Fernet.generate_key().decode())
    token = secrets_box.encrypt(SECRET)
    assert SECRET not in token and secrets_box.decrypt(token) == SECRET
    assert secrets_box.encrypt(SECRET) != token  # Fernet: у каждой записи своя «соль»
    assert secrets_box.hint(SECRET) == "••••abcd" and SECRET[:8] not in secrets_box.hint(SECRET)


def test_secrets_need_a_configured_key_and_detect_a_changed_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "secrets_encryption_key", "")
    assert secrets_box.is_configured() is False
    with pytest.raises(AppError) as exc:
        secrets_box.encrypt(SECRET)
    assert "SECRETS_ENCRYPTION_KEY" in exc.value.message
    monkeypatch.setattr(settings, "secrets_encryption_key", "не-ключ")
    assert secrets_box.is_configured() is False
    monkeypatch.setattr(settings, "secrets_encryption_key", Fernet.generate_key().decode())
    token = secrets_box.encrypt(SECRET)
    monkeypatch.setattr(settings, "secrets_encryption_key", Fernet.generate_key().decode())  # ключ шифрования сменился
    with pytest.raises(AppError) as changed:
        secrets_box.decrypt(token)
    assert "Введите ключ заново" in changed.value.message


# ---------------- цели и запросы к сервисам ----------------

def test_build_target_picks_the_right_protocol_and_hides_the_key():
    native = build_target(kind="yandex", model="yandexgpt-5.1", api_key=SECRET, folder_id="b1g")
    openai_on_yandex = build_target(kind="yandex", model="deepseek-v4-flash", api_key=SECRET, folder_id="b1g")
    other = build_target(kind="openai_compatible", model="claude-sonnet-5-5", api_key=SECRET, base_url="https://api.example.com/v1/")
    assert (native.kind, openai_on_yandex.kind, other.kind) == ("yandex_native", "yandex_openai", "openai_chat")
    assert native.log_uri == "gpt://b1g/yandexgpt-5.1" and other.log_uri == "api.example.com/claude-sonnet-5-5"
    for t in (native, openai_on_yandex, other):
        assert SECRET not in repr(t) and SECRET not in t.log_uri


class _Recorder:
    def __init__(self, response_json):
        self.requests = []
        self.response_json = response_json

    def transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, json=self.response_json)

        return httpx.MockTransport(handler)


def _patch_http(monkeypatch, rec):
    real = httpx.AsyncClient
    monkeypatch.setattr(llm_targets.httpx, "AsyncClient", lambda **kw: real(transport=rec.transport(), **kw))


@pytest.mark.asyncio
async def test_call_model_uses_the_native_yandex_api_for_yandexgpt(monkeypatch):
    rec = _Recorder({"result": {"alternatives": [{"message": {"text": "ответ"}}]}})
    _patch_http(monkeypatch, rec)
    target = build_target(kind="yandex", model="yandexgpt-5.1", api_key=SECRET, folder_id="b1g")
    assert await call_model(target, prompt="привет", timeout=5, max_tokens=100) == "ответ"
    req = rec.requests[0]
    assert str(req.url) == llm_targets.YANDEX_NATIVE_URL
    assert req.headers["authorization"] == f"Api-Key {SECRET}" and req.headers["x-folder-id"] == "b1g"
    body = req.read().decode()
    assert "gpt://b1g/yandexgpt-5.1" in body and '"maxTokens":"100"' in body.replace(" ", "")


@pytest.mark.asyncio
async def test_call_model_uses_openai_endpoint_for_open_models_on_yandex_and_for_other_services(monkeypatch):
    rec = _Recorder({"choices": [{"message": {"content": "ответ"}}]})
    _patch_http(monkeypatch, rec)
    yandex_open = build_target(kind="yandex", model="deepseek-v4-flash", api_key=SECRET, folder_id="b1g")
    assert await call_model(yandex_open, prompt="p", timeout=5, max_tokens=50) == "ответ"
    first = rec.requests[0]
    assert str(first.url) == llm_targets.YANDEX_OPENAI_URL and first.headers["authorization"] == f"Api-Key {SECRET}"
    assert "gpt://b1g/deepseek-v4-flash" in first.read().decode()

    other = build_target(kind="openai_compatible", model="claude-sonnet-5-5", api_key=SECRET, base_url="https://api.example.com/v1")
    assert await call_model(other, prompt="p", timeout=5, max_tokens=50) == "ответ"
    second = rec.requests[1]
    assert str(second.url) == "https://api.example.com/v1/chat/completions"
    assert second.headers["authorization"] == f"Bearer {SECRET}" and '"model":"claude-sonnet-5-5"' in second.read().decode().replace(" ", "")


@pytest.mark.asyncio
async def test_validate_base_url_rejects_unsafe_addresses(monkeypatch):
    for bad in ("http://api.example.com/v1", "https://user:pw@api.example.com/v1", "https://api.example.com/v1?key=1", "api.example.com", ""):
        with pytest.raises(AppError):
            await validate_base_url(bad)
    for internal in ("https://127.0.0.1/v1", "https://10.0.0.5/v1", "https://169.254.169.254/latest"):
        with pytest.raises(AppError) as exc:
            await validate_base_url(internal)
        assert "недопустим" in exc.value.message

    async def public(host, port):
        return "93.184.216.34"

    monkeypatch.setattr(llm_targets, "resolve_validated_ip", public)
    assert await validate_base_url(" https://api.example.com/v1/ ") == "https://api.example.com/v1"


# ---------------- загрузка настроек из БД ----------------

class _FakeSession:
    def __init__(self, rows, fail=False):
        self.rows, self.fail = rows, fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        if self.fail:
            raise RuntimeError("БД недоступна")
        return SimpleNamespace(all=lambda: self.rows)


def _row(stage, model, kind="yandex", secret=SECRET, folder="b1g", url=""):
    return (
        SimpleNamespace(stage=stage, model=model),
        SimpleNamespace(kind=kind, folder_id=folder, base_url=url, name="Подкл.", api_key_encrypted=secrets_box.encrypt(secret)),
    )


@pytest.mark.asyncio
async def test_load_ai_config_decrypts_keys_caches_and_survives_failures(monkeypatch, key):
    calls = {"n": 0}

    def factory_maker(rows, fail=False):
        def get_factory():
            def factory():
                calls["n"] += 1
                return _FakeSession(rows, fail)

            return factory

        return get_factory

    rows = [_row("draft", "yandexgpt-5-pro"), _row("fallback", "claude-sonnet-5-5", kind="openai_compatible", folder="", url="https://api.example.com/v1")]
    monkeypatch.setattr(llm_targets, "get_session_factory", factory_maker(rows))
    cfg = await llm_targets.load_ai_config()
    assert cfg.targets["draft"].api_key == SECRET and cfg.targets["draft"].kind == "yandex_native"
    assert cfg.fallback.kind == "openai_chat" and cfg.fallback.base_url == "https://api.example.com/v1"
    assert cfg.for_call_stage("draft") is cfg.targets["draft"] and cfg.for_call_stage("critique") is cfg.targets["draft"]
    assert cfg.for_call_stage("relevance") is None and cfg.for_call_stage("fact_passport") is None  # не заданы — настройки сервера

    await llm_targets.load_ai_config()
    assert calls["n"] == 1  # второй вызов — из кеша
    llm_targets.invalidate_cache()
    await llm_targets.load_ai_config()
    assert calls["n"] == 2

    llm_targets.invalidate_cache()
    monkeypatch.setattr(llm_targets, "get_session_factory", factory_maker(rows, fail=True))
    assert (await llm_targets.load_ai_config()).targets == {}  # сбой БД не ломает работу

    llm_targets.invalidate_cache()
    broken = [(SimpleNamespace(stage="facts", model="m"), SimpleNamespace(kind="yandex", folder_id="b1g", base_url="", name="X", api_key_encrypted="не-шифртекст"))]
    monkeypatch.setattr(llm_targets, "get_session_factory", factory_maker(broken))
    assert (await llm_targets.load_ai_config()).targets == {}  # нерасшифровываемый ключ — этап по настройкам сервера


# ---------------- провайдер: выбор модели на этапе ----------------

class _LlmDb:
    def __init__(self):
        self.added = []

    async def scalar(self, *_a, **_k):
        return 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


def _provider(monkeypatch, config, replies):
    settings = Settings(llm_provider="какой-то-другой", yandex_folder_id="", yandex_api_key="", llm_fixture_mode=False)
    provider = YandexGPTPro51Provider(settings)
    seen = []

    async def fake_call(target, *, prompt, stage):
        seen.append(target)
        return replies[target.model]

    async def load(**kw):
        return config

    monkeypatch.setattr(provider, "_call_target", fake_call)
    monkeypatch.setattr(yandex_gpt, "load_ai_config", load)
    return provider, seen


@pytest.mark.asyncio
async def test_stage_uses_the_model_from_admin_and_bypasses_env_checks(monkeypatch):
    draft_t = build_target(kind="yandex", model="yandexgpt-5-pro", api_key=SECRET, folder_id="b1g")
    config = AiConfig({"draft": draft_t})
    provider, seen = _provider(monkeypatch, config, {"yandexgpt-5-pro": GOOD_DRAFT})
    db = _LlmDb()
    result = await provider.draft_independent_text(db, workspace_id=uuid.uuid4(), news_item_id=None, title="Т", facts_text="- факт")
    assert isinstance(result, DraftResult) and result.model_used == "yandexgpt-5-pro"
    assert seen == [draft_t]
    assert db.added[0].model_uri == "gpt://b1g/yandexgpt-5-pro" and SECRET not in str(vars(db.added[0]))


@pytest.mark.asyncio
async def test_each_call_stage_gets_its_own_model(monkeypatch):
    targets = {
        "relevance": build_target(kind="yandex", model="yandexgpt-5-lite", api_key=SECRET, folder_id="b1g"),
        "facts": build_target(kind="openai_compatible", model="facts-model", api_key=SECRET, base_url="https://api.example.com/v1"),
        "draft": build_target(kind="yandex", model="yandexgpt-5-pro", api_key=SECRET, folder_id="b1g"),
    }
    replies = {
        "yandexgpt-5-lite": '{"is_relevant": true, "reasoning": "ок"}',
        "facts-model": '{"statements": [{"statement": "факт", "status": "confirmed", "source_fragment": "цитата"}]}',
        "yandexgpt-5-pro": GOOD_DRAFT,
    }
    provider, seen = _provider(monkeypatch, AiConfig(targets), replies)
    ids = dict(workspace_id=uuid.uuid4(), news_item_id=None)
    rel = await provider.check_relevance(_LlmDb(), title="Т", theme="Тема", **ids)
    facts = await provider.build_fact_passport(_LlmDb(), source_fragment="текст", **ids)
    draft = await provider.draft_independent_text(_LlmDb(), title="Т", facts_text="- факт", **ids)
    assert isinstance(facts, FactPassportResult)
    assert (rel.model_used, facts.model_used, draft.model_used) == ("yandexgpt-5-lite", "facts-model", "yandexgpt-5-pro")


@pytest.mark.asyncio
async def test_admin_fallback_is_used_on_refusal(monkeypatch):
    primary = build_target(kind="yandex", model="yandexgpt-5.1", api_key=SECRET, folder_id="b1g")
    fallback = build_target(kind="openai_compatible", model="claude-sonnet-5-5", api_key=SECRET, base_url="https://api.example.com/v1")
    config = AiConfig({"draft": primary, "fallback": fallback})
    replies = {"yandexgpt-5.1": "Я не могу обсуждать эту тему. Давайте поговорим о чём-нибудь ещё.", "claude-sonnet-5-5": GOOD_DRAFT}
    provider, seen = _provider(monkeypatch, config, replies)
    db = _LlmDb()
    result = await provider.draft_independent_text(db, workspace_id=uuid.uuid4(), news_item_id=None, title="Т", facts_text="- факт")
    assert result.model_used == "claude-sonnet-5-5" and [t.model for t in seen] == ["yandexgpt-5.1", "claude-sonnet-5-5"]
    assert [c.model_uri for c in db.added] == ["gpt://b1g/yandexgpt-5.1", "api.example.com/claude-sonnet-5-5"]


@pytest.mark.asyncio
async def test_without_admin_config_server_settings_still_apply_with_their_checks(monkeypatch):
    provider, _seen = _provider(monkeypatch, AiConfig(), {})  # llm_provider в настройках сервера «чужой» и фикстур нет
    with pytest.raises(AppError) as exc:
        await provider.draft_independent_text(_LlmDb(), workspace_id=uuid.uuid4(), news_item_id=None, title="Т", facts_text="- факт")
    assert exc.value.code == "LLM_NOT_CONFIGURED"


# ---------------- сервис админки ----------------

class MemDb:
    """Минимальная БД в памяти для сервиса: подключения и модели по этапам."""

    def __init__(self):
        self.conns: dict = {}
        self.stages: dict = {}

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        (self.conns if isinstance(obj, AiConnection) else self.stages)[obj.id] = obj

    async def commit(self):
        pass

    async def get(self, model, key):
        return (self.conns if model is AiConnection else self.stages).get(key)

    async def delete(self, obj):
        (self.conns if isinstance(obj, AiConnection) else self.stages).pop(obj.id, None)

    async def scalar(self, stmt):
        params = stmt.compile().params
        if "FROM ai_stage_models" in str(stmt):
            stage = next(v for k, v in params.items() if k.startswith("stage_"))
            return next((r for r in self.stages.values() if r.stage == stage), None)
        raise AssertionError(str(stmt))

    async def scalars(self, stmt):
        text, params = str(stmt), stmt.compile().params
        if "FROM ai_connections" in text:
            rows = list(self.conns.values())
        elif any(k.startswith("connection_id") for k in params):
            cid = next(v for k, v in params.items() if k.startswith("connection_id"))
            rows = [r for r in self.stages.values() if r.connection_id == cid]
        else:
            rows = list(self.stages.values())
        return SimpleNamespace(all=lambda: rows)


@pytest.fixture
def service(monkeypatch, key):
    events = []

    async def log(db, **kw):
        events.append(kw)

    monkeypatch.setattr(ai_admin_service, "log_activity", log)
    return SimpleNamespace(db=MemDb(), events=events, user=uuid.uuid4(), ws=uuid.uuid4())


async def _create(service, **kw):
    payload = ConnectionCreate(**{"name": "Яндекс", "kind": "yandex", "api_key": SECRET, "folder_id": "b1gabcdef", **kw})
    return await ai_admin_service.create_connection(service.db, payload=payload, user_id=service.user, workspace_id=service.ws)


@pytest.mark.asyncio
async def test_create_connection_encrypts_the_key_and_never_returns_it(service):
    conn = await _create(service)
    assert SECRET not in str(vars(conn)) and secrets_box.decrypt(conn.api_key_encrypted) == SECRET
    out = ai_admin_service.connection_out(conn)
    assert out.key_hint == "••••abcd" and SECRET not in out.model_dump_json()
    assert SECRET not in str(service.events) and service.events[0]["action"] == "ai_connection_created"


@pytest.mark.asyncio
async def test_create_connection_validates_required_fields_and_address(service, monkeypatch):
    with pytest.raises(AppError):
        await _create(service, folder_id="")  # у Яндекса обязателен каталог
    with pytest.raises(AppError):
        await _create(service, kind="openai_compatible", folder_id="", base_url="")
    with pytest.raises(AppError):
        await _create(service, kind="openai_compatible", folder_id="", base_url="https://127.0.0.1/v1")  # внутренний адрес
    for bad_key in ("abc12", "ключ-на-кириллице-1234", "key with spaces inside", "line\nbreak-key-12345"):
        with pytest.raises(ValueError):
            ConnectionCreate(name="x", kind="yandex", api_key=bad_key, folder_id="b1gabcdef")
    with pytest.raises(ValueError):
        ConnectionUpdate(api_key="ключ-на-кириллице-1234")

    async def public(host, port):
        return "93.184.216.34"

    monkeypatch.setattr(llm_targets, "resolve_validated_ip", public)
    ok = await _create(service, kind="openai_compatible", folder_id="", base_url="https://api.example.com/v1/")
    assert ok.base_url == "https://api.example.com/v1" and ok.folder_id == ""
    assert service.db.conns == {ok.id: ok}  # неудачные попытки ничего не сохранили


@pytest.mark.asyncio
async def test_create_without_encryption_key_saves_nothing(service, monkeypatch):
    monkeypatch.setattr(get_settings(), "secrets_encryption_key", "")
    with pytest.raises(AppError) as exc:
        await _create(service)
    assert "SECRETS_ENCRYPTION_KEY" in exc.value.message and service.db.conns == {}


@pytest.mark.asyncio
async def test_update_replaces_the_key_only_when_given(service):
    conn = await _create(service)
    old = conn.api_key_encrypted
    await ai_admin_service.update_connection(
        service.db, connection_id=conn.id, payload=ConnectionUpdate(name="Новое имя"), user_id=service.user, workspace_id=service.ws
    )
    assert conn.name == "Новое имя" and conn.api_key_encrypted == old and service.events[-1]["details"]["key_replaced"] is False
    await ai_admin_service.update_connection(
        service.db, connection_id=conn.id, payload=ConnectionUpdate(api_key="sk-NEW-KEY-9999zzzz"), user_id=service.user, workspace_id=service.ws
    )
    assert secrets_box.decrypt(conn.api_key_encrypted) == "sk-NEW-KEY-9999zzzz" and conn.key_hint == "••••zzzz"
    assert service.events[-1]["details"]["key_replaced"] is True and "NEW-KEY" not in str(service.events)


@pytest.mark.asyncio
async def test_stages_are_set_reset_and_validated(service):
    conn = await _create(service)
    other = SimpleNamespace(connection_id=conn.id)
    payload = StagesUpdate(stages={"draft": StageIn(connection_id=conn.id, model="yandexgpt-5-pro"), "fallback": StageIn(connection_id=conn.id, model="deepseek-v4-flash")})
    await ai_admin_service.set_stages(service.db, payload=payload, user_id=service.user, workspace_id=service.ws)
    assert {r.stage: r.model for r in service.db.stages.values()} == {"draft": "yandexgpt-5-pro", "fallback": "deepseek-v4-flash"}
    await ai_admin_service.set_stages(  # изменение существующего и сброс другого
        service.db, payload=StagesUpdate(stages={"draft": StageIn(connection_id=conn.id, model="yandexgpt-5.1"), "fallback": None}),
        user_id=service.user, workspace_id=service.ws,
    )
    assert {r.stage: r.model for r in service.db.stages.values()} == {"draft": "yandexgpt-5.1"}
    with pytest.raises(AppError):
        await ai_admin_service.set_stages(service.db, payload=StagesUpdate(stages={"magic": None}), user_id=service.user, workspace_id=service.ws)
    with pytest.raises(AppError):
        await ai_admin_service.set_stages(
            service.db, payload=StagesUpdate(stages={"facts": StageIn(connection_id=uuid.uuid4(), model="m")}), user_id=service.user, workspace_id=service.ws
        )
    with pytest.raises(ValueError):
        StageIn(connection_id=conn.id, model="плохое имя; drop")
    assert other and service.events[-1]["action"] == "ai_stages_updated"


@pytest.mark.asyncio
async def test_connection_in_use_cannot_be_deleted_and_overview_shows_sources(service, monkeypatch):
    conn = await _create(service)
    await ai_admin_service.set_stages(
        service.db, payload=StagesUpdate(stages={"draft": StageIn(connection_id=conn.id, model="yandexgpt-5-pro")}), user_id=service.user, workspace_id=service.ws
    )
    with pytest.raises(AppError) as exc:
        await ai_admin_service.delete_connection(service.db, connection_id=conn.id, user_id=service.user, workspace_id=service.ws)
    assert "Черновик" in exc.value.message and conn.id in service.db.conns

    monkeypatch.setattr(get_settings(), "llm_fixture_mode", True)
    monkeypatch.setattr(get_settings(), "yandex_api_key", "")
    view = await ai_admin_service.overview(service.db)
    by_stage = {e.stage: e for e in view.effective}
    assert by_stage["draft"].source == "admin" and by_stage["draft"].model == "yandexgpt-5-pro" and by_stage["draft"].connection_name == "Яндекс"
    assert by_stage["relevance"].source == "fixture"  # в админке не задано, настроек сервера нет, включён демо-режим
    assert view.encryption_configured is True and SECRET not in view.model_dump_json()

    await ai_admin_service.set_stages(service.db, payload=StagesUpdate(stages={"draft": None}), user_id=service.user, workspace_id=service.ws)
    await ai_admin_service.delete_connection(service.db, connection_id=conn.id, user_id=service.user, workspace_id=service.ws)
    assert service.db.conns == {} and service.events[-1]["action"] == "ai_connection_deleted"


@pytest.mark.asyncio
async def test_test_connection_reports_success_and_readable_errors_without_leaking_the_key(service, monkeypatch):
    conn = await _create(service)
    results = iter(["ок", httpx.HTTPStatusError("x", request=httpx.Request("POST", "https://x"), response=httpx.Response(401)), httpx.ConnectTimeout("t")])

    async def fake_call(target, *, prompt, timeout, max_tokens, temperature=0.2):
        assert target.api_key == SECRET and target.model == "yandexgpt-5.1"  # ключ расшифрован только внутри вызова
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ai_admin_service, "call_model", fake_call)
    ok = await ai_admin_service.test_connection(service.db, connection_id=conn.id, model="yandexgpt-5.1", user_id=service.user, workspace_id=service.ws)
    assert ok.ok is True and conn.last_check_ok is True
    bad = await ai_admin_service.test_connection(service.db, connection_id=conn.id, model="yandexgpt-5.1", user_id=service.user, workspace_id=service.ws)
    assert bad.ok is False and "401" in bad.note and "ключ не принят" in bad.note and conn.last_check_ok is False
    down = await ai_admin_service.test_connection(service.db, connection_id=conn.id, model="yandexgpt-5.1", user_id=service.user, workspace_id=service.ws)
    assert down.ok is False and "недоступен" in down.note
    assert SECRET not in str(service.events) and SECRET not in conn.last_check_note and SECRET not in bad.model_dump_json()


# ---------------- HTTP: доступ только администратору, ключи в схемах не отдаются ----------------

@pytest.mark.asyncio
async def test_admin_ai_endpoints_require_a_platform_admin_session():
    from app.main import create_app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        assert (await client.get("/api/v1/admin/ai")).status_code == 401
        assert (await client.post("/api/v1/admin/ai/connections", json={"name": "x", "kind": "yandex", "api_key": SECRET, "folder_id": "b1gabcdef"})).status_code == 401
        assert (await client.put("/api/v1/admin/ai/stages", json={"stages": {}})).status_code == 401
        assert (await client.delete(f"/api/v1/admin/ai/connections/{uuid.uuid4()}")).status_code == 401


def test_response_schemas_never_contain_the_api_key_field():
    from app.main import create_app

    schemas = create_app().openapi()["components"]["schemas"]
    for name in ("ConnectionOut", "AiOverviewOut", "StageOut", "EffectiveOut", "TestResultOut"):
        props = schemas[name]["properties"]
        assert not any("api_key" in p or "secret" in p for p in props), name
    assert "key_hint" in schemas["ConnectionOut"]["properties"]
