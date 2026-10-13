import hashlib
import json
import time
import uuid
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.errors import AppError
from app.models import LlmCall
from app.providers.llm_targets import ModelTarget, call_model, load_ai_config

PROMPT_VERSION = "v2"

# ТЗ §10: единственная разрешённая модель. Никогда не заменяется тихо на другую.
_EXPECTED_PROVIDER = "yandexgpt_pro_5_1"
_PRIMARY_MODEL = "yandexgpt-5.1"

# Запасная модель включается только явной настройкой LLM_FALLBACK_MODEL и только из этого
# списка (открытые модели, размещённые в том же облаке Яндекса). Любое другое значение —
# ошибка конфигурации, а не молчаливая подмена (ТЗ §10).
ALLOWED_FALLBACK_MODELS = frozenset({"deepseek-v4-flash"})


class RelevanceResult(BaseModel):
    is_relevant: bool
    reasoning: str
    model_used: str | None = Field(default=None, exclude=True)


class FactStatement(BaseModel):
    statement: str
    status: str  # confirmed | unknown | needs_review | contradictory
    source_fragment: str


class FactPassportResult(BaseModel):
    statements: list[FactStatement]
    model_used: str | None = Field(default=None, exclude=True)


class DraftResult(BaseModel):
    title: str
    text: str
    model_used: str | None = Field(default=None, exclude=True)


class CritiqueResult(BaseModel):
    unconfirmed_claims: list[str]
    distortions: list[str]
    similarity_concerns: list[str]
    model_used: str | None = Field(default=None, exclude=True)


class LlmStageError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _input_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _strip_markdown_json_fence(text: str) -> str:
    """Models asked for raw JSON sometimes still wrap it in a ```json ... ``` fence
    regardless of instructions — strip that before validation instead of failing."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        stripped = stripped.removesuffix("```").strip()
    return stripped


# YandexGPT на чувствительные темы (санкции, политика и т.п.) отвечает не JSON-ом, а
# стандартной фразой-отказом — это не сбой формата, и пользователю нужно сказать об
# этом прямо, а не показывать текст про JSON-схему.
_REFUSAL_MARKERS = (
    "не могу обсуждать эту тему",
    "не могу говорить на эту тему",
    "давайте поговорим о чём-нибудь ещё",
    "давайте поговорим о чем-нибудь еще",
)


def _looks_like_refusal(text: str) -> bool:
    stripped = text.strip()
    if stripped.startswith(("{", "[", "```")):
        return False
    low = stripped.lower()
    return any(marker in low for marker in _REFUSAL_MARKERS)


class YandexGPTPro51Provider:
    """Единственный провайдер ИИ в MVP (ТЗ §10). Каждый вызов — новая HTTP-сессия,
    все параметры модели берутся из настроек сервера, ключ никогда не логируется."""

    name = "yandexgpt_pro_5_1"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _require_configured_or_fixture(self) -> bool:
        """Returns True if this call must run in fixture mode. Raises LLM_NOT_CONFIGURED
        when neither a real, verified configuration nor explicit fixture mode is present —
        there is no third option, and no fallback to a different model or URI."""
        if self.settings.llm_provider != _EXPECTED_PROVIDER:
            raise AppError(
                "LLM_NOT_CONFIGURED",
                f"LLM_PROVIDER должен быть {_EXPECTED_PROVIDER!r}, задано {self.settings.llm_provider!r}.",
            )
        if self.settings.llm_is_configured():
            return False
        if self.settings.llm_fixture_mode:
            return True
        raise AppError(
            "LLM_NOT_CONFIGURED",
            "YandexGPT Pro 5.1 не настроен (нет YANDEX_FOLDER_ID/YANDEX_API_KEY или "
            "YANDEX_MODEL_URI не совпадает с YANDEX_FOLDER_ID) и фикстурный режим выключен.",
        )

    def _env_primary_target(self) -> ModelTarget:
        """Основная модель из настроек сервера (переменные окружения) — если для этапа в админке ничего не задано."""
        model = self.settings.yandex_model_uri.rsplit("/", 1)[-1] or _PRIMARY_MODEL
        return ModelTarget("yandex_native", model, self.settings.yandex_api_key, folder_id=self.settings.yandex_folder_id, source="env")

    def _env_fallback_target(self) -> ModelTarget | None:
        model = self._fallback_model()
        if model is None:
            return None
        return ModelTarget("yandex_openai", model, self.settings.yandex_api_key, folder_id=self.settings.yandex_folder_id, source="env")

    async def _call_target(self, target: ModelTarget, *, prompt: str, stage: str) -> str:
        return await call_model(
            target, prompt=prompt, timeout=self.settings.llm_timeout_seconds, max_tokens=self.settings.llm_max_tokens
        )

    def _fallback_model(self) -> str | None:
        model = (self.settings.llm_fallback_model or "").strip()
        if not model:
            return None
        if model not in ALLOWED_FALLBACK_MODELS:
            raise AppError(
                "LLM_NOT_CONFIGURED",
                f"LLM_FALLBACK_MODEL={model!r} не входит в список разрешённых запасных моделей "
                f"({', '.join(sorted(ALLOWED_FALLBACK_MODELS))}).",
            )
        return model

    async def _enforce_daily_budget(self, db, *, workspace_id: uuid.UUID) -> None:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        used = await db.scalar(
            select(func.coalesce(func.sum(LlmCall.input_tokens + LlmCall.output_tokens), 0)).where(
                LlmCall.workspace_id == workspace_id, LlmCall.created_at >= today_start
            )
        )
        if used >= self.settings.llm_token_budget_per_workspace_day:
            raise AppError(
                "LLM_BUDGET_EXCEEDED",
                f"Дневной бюджет токенов для рабочего пространства исчерпан ({used}/"
                f"{self.settings.llm_token_budget_per_workspace_day}).",
            )

    async def _attempt(
        self,
        db,
        *,
        workspace_id: uuid.UUID,
        news_item_id: uuid.UUID | None,
        stage: str,
        prompt: str,
        result_model: type[BaseModel],
        fixture_response: dict,
        use_fixture: bool,
        target: ModelTarget | None,
    ) -> tuple[BaseModel | None, bool, str]:
        """Один вызов модели (основной или запасной) + строка в llm_calls. Возвращает
        (результат или None, «модель отказалась», текст ошибки)."""
        request_id = str(uuid.uuid4())
        started = time.monotonic()
        error = ""
        raw_text = ""
        refused = False
        parsed = None
        model_name = target.model if target is not None else _PRIMARY_MODEL
        if use_fixture:
            model_uri_logged = f"FIXTURE:{self.settings.yandex_model_uri or 'yandexgpt-5.1'}"
        else:
            model_uri_logged = target.log_uri

        try:
            if use_fixture:
                raw_text = json.dumps(fixture_response, ensure_ascii=False)
            else:
                raw_text = await self._call_target(target, prompt=prompt, stage=stage)
            if not use_fixture and _looks_like_refusal(raw_text):
                refused = True
                error = "model_refused: модель отказалась обрабатывать материал"
            else:
                parsed = result_model.model_validate_json(_strip_markdown_json_fence(raw_text))
                parsed.model_used = "FIXTURE" if use_fixture else model_name
        except (ValidationError, json.JSONDecodeError, KeyError) as exc:
            error = f"invalid_json_or_schema: {exc}"
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            error = f"http_error: {type(exc).__name__}{f' HTTP {status}' if status else ''} {exc}".strip()

        duration_ms = int((time.monotonic() - started) * 1000)
        db.add(
            LlmCall(
                workspace_id=workspace_id,
                news_item_id=news_item_id,
                stage=stage,
                model_uri=model_uri_logged,
                prompt_version=PROMPT_VERSION,
                request_id=request_id,
                input_hash=_input_hash(prompt),
                input_tokens=len(prompt.split()),
                output_tokens=len(raw_text.split()) if raw_text else 0,
                cost_rub=0 if use_fixture else 0,  # реальный тариф уточняется у заказчика (ТЗ §15)
                duration_ms=duration_ms,
                error=error,
                created_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
        return parsed, refused, error

    async def _attempt_with_retry(self, db, *, target: ModelTarget | None, **common):
        """Один повтор на той же модели, если ответ — не валидный JSON (например, неэкранированная
        кавычка в тексте). Отказ модели и сетевые ошибки повтором не лечатся."""
        parsed, refused, error = await self._attempt(db, target=target, **common)
        if parsed is None and not refused and error.startswith("invalid_json_or_schema"):
            parsed, refused, error = await self._attempt(db, target=target, **common)
        return parsed, refused, error

    async def _run_stage(
        self,
        db,
        *,
        workspace_id: uuid.UUID,
        news_item_id: uuid.UUID | None,
        stage: str,
        prompt: str,
        result_model: type[BaseModel],
        fixture_response: dict,
    ) -> BaseModel:
        """Модель этапа берётся из админки (подключение + модель по этапам); если для этапа там ничего не
        задано — из настроек сервера (YandexGPT Pro 5.1 по переменным окружения, прежнее поведение со
        всеми его проверками). Только если основная модель не дала годного ответа — ОТКАЗАЛАСЬ (фильтр на
        чувствительные темы) или дважды подряд вернула невалидный JSON — и задана запасная (в админке или
        LLM_FALLBACK_MODEL), тот же запрос повторяется на запасной. Все вызовы пишутся в llm_calls с точным
        URI модели (без ключа); результат помечен model_used. Сетевые ошибки запасной моделью НЕ «лечатся»."""
        ai_config = await load_ai_config()
        primary = ai_config.for_call_stage(stage)
        if primary is not None:
            use_fixture = False
        else:
            use_fixture = self._require_configured_or_fixture()
            primary = None if use_fixture else self._env_primary_target()
        fallback = None if use_fixture else (ai_config.fallback or self._env_fallback_target())
        await self._enforce_daily_budget(db, workspace_id=workspace_id)

        common = dict(
            workspace_id=workspace_id, news_item_id=news_item_id, stage=stage, prompt=prompt,
            result_model=result_model, fixture_response=fixture_response, use_fixture=use_fixture,
        )
        parsed, refused, error = await self._attempt_with_retry(db, target=primary, **common)
        if parsed is not None:
            return parsed

        broken_json = error.startswith("invalid_json_or_schema")
        if (refused or broken_json) and fallback is not None:
            parsed, refused, error = await self._attempt_with_retry(db, target=fallback, **common)
            if parsed is not None:
                return parsed

        if refused:
            raise AppError(
                "VALIDATION_ERROR",
                "ИИ отказался обрабатывать этот материал — вероятно, из-за чувствительной "
                "темы (например, санкции или политика). Заполните текст вручную или отклоните материал.",
            )
        raise AppError(
            "VALIDATION_ERROR",
            f"Ответ модели на этапе {stage!r} не прошёл проверку структуры: {error}",
        )

    async def check_relevance(self, db, *, workspace_id: uuid.UUID, news_item_id: uuid.UUID | None, title: str, theme: str) -> RelevanceResult:
        prompt = (
            f"[{PROMPT_VERSION}:relevance] Тема потока: {theme}\nЗаголовок: {title}\n\n"
            "Определи, относится ли заголовок к теме потока. "
            'Ответь СТРОГО одним JSON-объектом без markdown и пояснений вне JSON, '
            'по схеме: {"is_relevant": true|false, "reasoning": "краткое обоснование на русском"}.'
        )
        fixture = {"is_relevant": True, "reasoning": "DEMO_FIXTURE: заголовок соответствует теме потока."}
        return await self._run_stage(
            db, workspace_id=workspace_id, news_item_id=news_item_id, stage="relevance",
            prompt=prompt, result_model=RelevanceResult, fixture_response=fixture,
        )

    async def build_fact_passport(self, db, *, workspace_id: uuid.UUID, news_item_id: uuid.UUID, source_fragment: str) -> FactPassportResult:
        prompt = (
            f"[{PROMPT_VERSION}:facts] Извлеки юридически значимые факты из фрагмента, "
            f"не придумывая дат/номеров дел:\n{source_fragment}\n\n"
            'Ответь СТРОГО одним JSON-объектом без markdown и пояснений вне JSON, по схеме: '
            '{"statements": [{"statement": "формулировка факта", '
            '"status": "confirmed|unknown|needs_review|contradictory", '
            '"source_fragment": "цитата из фрагмента, подтверждающая факт"}]}.'
        )
        fixture = {
            "statements": [
                {
                    "statement": "Событие описано в учебном фрагменте, требует проверки по официальному документу.",
                    "status": "unknown",
                    "source_fragment": source_fragment[:200],
                }
            ]
        }
        return await self._run_stage(
            db, workspace_id=workspace_id, news_item_id=news_item_id, stage="fact_passport",
            prompt=prompt, result_model=FactPassportResult, fixture_response=fixture,
        )

    async def draft_independent_text(
        self, db, *, workspace_id: uuid.UUID, news_item_id: uuid.UUID, title: str, facts_text: str,
        rewrite_hint: str | None = None,
    ) -> DraftResult:
        hint_block = f"{rewrite_hint}\n\n" if rewrite_hint else ""
        prompt = (
            f"[{PROMPT_VERSION}:draft] Напиши самостоятельный текст по фактам "
            f"(не копируя источник дословно):\nЗаголовок: {title}\nФакты: {facts_text}\n\n"
            "Требования к самостоятельности: ни одно предложение не должно повторять формулировки "
            "фактов или источника — каждое строй заново, меняй порядок и подачу (от причины к следствию, "
            "от общего к частному), объединяй и разделяй факты. Дословно допускаются только названия, "
            "числа, даты, номера дел и ссылки на нормы; не используй подряд больше пяти слов из "
            "формулировок фактов.\n\n"
            f"{hint_block}"
            "Стиль — академический юридический: нейтральный, точный, без разговорных оборотов "
            "и эмоциональных оценок; термины употребляй в их устоявшемся правовом значении; "
            "где уместно, ссылайся на вид нормы или акта, не придумывая номеров и дат, которых "
            "нет в фактах. Пиши от третьего лица, как аналитический материал для юристов, а не "
            "как новостная заметка.\n\n"
            'Ответь СТРОГО одним JSON-объектом без markdown и пояснений вне JSON, по схеме: '
            '{"title": "заголовок материала", "text": "текст материала"}.'
        )
        fixture = {
            "title": title,
            "text": (
                "DEMO_FIXTURE: черновик подготовлен без реального подключения модели. "
                "Требуется правка и проверка юристом перед утверждением."
            ),
        }
        return await self._run_stage(
            db, workspace_id=workspace_id, news_item_id=news_item_id, stage="draft",
            prompt=prompt, result_model=DraftResult, fixture_response=fixture,
        )

    async def critique(self, db, *, workspace_id: uuid.UUID, news_item_id: uuid.UUID, text: str, facts_text: str) -> CritiqueResult:
        prompt = (
            f"[{PROMPT_VERSION}:critique] Найди неподтверждённые утверждения, искажения фактов и "
            f"текстовые совпадения с источником:\nТекст: {text}\nФакты: {facts_text}\n\n"
            'Ответь СТРОГО одним JSON-объектом без markdown и пояснений вне JSON, по схеме: '
            '{"unconfirmed_claims": ["..."], "distortions": ["..."], "similarity_concerns": ["..."]}. '
            "Пустые списки — если проблем не найдено."
        )
        fixture = {"unconfirmed_claims": [], "distortions": [], "similarity_concerns": []}
        result = await self._run_stage(
            db, workspace_id=workspace_id, news_item_id=news_item_id, stage="critique",
            prompt=prompt, result_model=CritiqueResult, fixture_response=fixture,
        )
        return result
