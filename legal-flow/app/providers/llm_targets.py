"""Какая модель и с каким ключом работает на каждом этапе.

Источник настроек — подключения и модели по этапам, которые админ платформы задаёт в интерфейсе
(таблицы ai_connections / ai_stage_models). Если для этапа в админке ничего не задано, провайдер берёт
настройки сервера из переменных окружения (прежнее поведение). Ключи расшифровываются только здесь, в
памяти процесса, и нигде не логируются (repr цели скрывает ключ)."""

import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from app import secrets_box
from app.db import get_session_factory
from app.errors import AppError
from app.models import AiConnection, AiStageModel
from app.providers.ssrf import SSRFBlockedError, resolve_validated_ip

logger = logging.getLogger("app.providers.llm_targets")

YANDEX_NATIVE_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEX_OPENAI_URL = "https://llm.api.cloud.yandex.net/v1/chat/completions"

CONNECTION_KINDS = ("yandex", "openai_compatible")
ADMIN_STAGES = ("relevance", "facts", "draft", "fallback")
# этап вызова в провайдере -> этап настройки в админке (самокритика работает на модели черновика)
STAGE_FOR_CALL = {"relevance": "relevance", "fact_passport": "facts", "draft": "draft", "critique": "draft"}
YANDEX_MODEL_SUGGESTIONS = [
    "yandexgpt-5.1", "yandexgpt-5-pro", "yandexgpt-5-lite", "aliceai-llm", "deepseek-v4-flash", "gpt-oss-120b",
    "gpt-oss-20b", "qwen3-235b-a22b-fp8", "qwen3.6-35b-a3b",
]
_CACHE_TTL_SECONDS = 20.0
_ERROR_TTL_SECONDS = 5.0


@dataclass(frozen=True)
class ModelTarget:
    kind: str  # yandex_native | yandex_openai | openai_chat
    model: str
    api_key: str = field(default="", repr=False)
    folder_id: str = ""
    base_url: str = ""
    source: str = "env"  # env | admin
    connection_name: str = ""

    @property
    def log_uri(self) -> str:
        """Что пишется в llm_calls.model_uri: без ключа, чтобы подмена модели была видна в журнале."""
        if self.kind == "openai_chat":
            host = urlsplit(self.base_url).netloc
            return f"{host}/{self.model}"
        return f"gpt://{self.folder_id}/{self.model}"


@dataclass
class AiConfig:
    targets: dict[str, ModelTarget] = field(default_factory=dict)  # только этапы, заданные в админке

    def for_call_stage(self, call_stage: str) -> ModelTarget | None:
        return self.targets.get(STAGE_FOR_CALL.get(call_stage, "draft"))

    @property
    def fallback(self) -> ModelTarget | None:
        return self.targets.get("fallback")


def build_target(
    *, kind: str, model: str, api_key: str, folder_id: str = "", base_url: str = "", source: str = "admin", connection_name: str = ""
) -> ModelTarget:
    """Yandex: модели YandexGPT — через нативный API, остальные (открытые модели каталога) — через
    OpenAI-совместимый эндпоинт Яндекса. Любой другой сервис — OpenAI-совместимый /chat/completions."""
    if kind == "yandex":
        target_kind = "yandex_native" if model.startswith("yandexgpt") else "yandex_openai"
        return ModelTarget(target_kind, model, api_key, folder_id=folder_id, source=source, connection_name=connection_name)
    return ModelTarget("openai_chat", model, api_key, base_url=base_url.rstrip("/"), source=source, connection_name=connection_name)


async def call_model(target: ModelTarget, *, prompt: str, timeout: float, max_tokens: int, temperature: float = 0.2) -> str:
    """Один вызов модели; возвращает текст ответа. Исключения httpx пробрасываются вызывающему."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        if target.kind == "yandex_native":
            resp = await client.post(
                YANDEX_NATIVE_URL,
                headers={"Authorization": f"Api-Key {target.api_key}", "x-folder-id": target.folder_id},
                json={
                    "modelUri": f"gpt://{target.folder_id}/{target.model}",
                    "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": str(max_tokens)},
                    "messages": [{"role": "user", "text": prompt}],
                },
            )
            resp.raise_for_status()
            return resp.json()["result"]["alternatives"][0]["message"]["text"]
        if target.kind == "yandex_openai":
            url, headers, model = YANDEX_OPENAI_URL, {"Authorization": f"Api-Key {target.api_key}"}, f"gpt://{target.folder_id}/{target.model}"
        else:
            url, headers, model = f"{target.base_url}/chat/completions", {"Authorization": f"Bearer {target.api_key}"}, target.model
        resp = await client.post(
            url, headers=headers,
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": temperature, "max_tokens": max_tokens},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"].get("content") or ""


async def validate_base_url(raw: str) -> str:
    """Адрес OpenAI-совместимого сервиса: только https, без логина/параметров, имя должно вести на
    публичный IP (защита от обращения к внутренним адресам сервера). Возвращает нормализованный адрес."""
    parts = urlsplit((raw or "").strip())
    if parts.scheme != "https" or not parts.hostname:
        raise AppError("VALIDATION_ERROR", "Адрес API должен начинаться с https:// и содержать имя сервера.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise AppError("VALIDATION_ERROR", "В адресе API не должно быть логина, параметров и якоря.")
    try:
        await resolve_validated_ip(parts.hostname, parts.port or 443)
    except SSRFBlockedError as exc:
        raise AppError("VALIDATION_ERROR", f"Адрес API недопустим: {exc.reason}") from exc
    return f"https://{parts.netloc}{parts.path}".rstrip("/")


_cache: tuple[float, AiConfig] | None = None


def invalidate_cache() -> None:
    global _cache
    _cache = None


async def load_ai_config(*, force: bool = False) -> AiConfig:
    """Настройки админки (кеш 20 с — воркер и сайт подхватывают смену без перезапуска). Читаются в отдельной
    сессии БД, чтобы не трогать транзакцию вызывающего кода. Сбой чтения не ломает работу: используются
    настройки сервера."""
    global _cache
    now = time.monotonic()
    if not force and _cache is not None and _cache[0] > now:
        return _cache[1]
    targets: dict[str, ModelTarget] = {}
    ttl = _CACHE_TTL_SECONDS
    try:
        async with get_session_factory()() as session:
            rows = (
                await session.execute(
                    select(AiStageModel, AiConnection).join(AiConnection, AiConnection.id == AiStageModel.connection_id)
                )
            ).all()
        for stage_row, conn in rows:
            try:
                key = secrets_box.decrypt(conn.api_key_encrypted)
            except AppError as exc:
                logger.warning("этап %s: ключ подключения %r не расшифрован (%s) — этап работает по настройкам сервера", stage_row.stage, conn.name, exc.message)
                continue
            targets[stage_row.stage] = build_target(
                kind=conn.kind, model=stage_row.model, api_key=key, folder_id=conn.folder_id, base_url=conn.base_url,
                source="admin", connection_name=conn.name,
            )
    except Exception:  # noqa: BLE001 -- ИИ-настройки не должны ронять сбор и черновики
        logger.exception("не удалось прочитать ИИ-настройки админки — работаем по настройкам сервера")
        ttl = _ERROR_TTL_SECONDS
    config = AiConfig(targets)
    _cache = (now + ttl, config)
    return config
