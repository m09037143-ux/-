import hashlib
import json
import time
import uuid
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.errors import AppError
from app.models import LlmCall

PROMPT_VERSION = "v1"

# ТЗ §10: единственная разрешённая модель. Никогда не заменяется тихо на другую.
_EXPECTED_PROVIDER = "yandexgpt_pro_5_1"


class RelevanceResult(BaseModel):
    is_relevant: bool
    reasoning: str


class FactStatement(BaseModel):
    statement: str
    status: str  # confirmed | unknown | needs_review | contradictory
    source_fragment: str


class FactPassportResult(BaseModel):
    statements: list[FactStatement]


class DraftResult(BaseModel):
    title: str
    text: str


class CritiqueResult(BaseModel):
    unconfirmed_claims: list[str]
    distortions: list[str]
    similarity_concerns: list[str]


class LlmStageError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _input_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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

    async def _call_real_model(self, *, prompt: str, stage: str) -> str:
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {
            "Authorization": f"Api-Key {self.settings.yandex_api_key}",
            "x-folder-id": self.settings.yandex_folder_id,
        }
        body = {
            "modelUri": self.settings.yandex_model_uri,
            "completionOptions": {"stream": False, "temperature": 0.2, "maxTokens": "2000"},
            "messages": [{"role": "user", "text": prompt}],
        }
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        return data["result"]["alternatives"][0]["message"]["text"]

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
        use_fixture = self._require_configured_or_fixture()
        await self._enforce_daily_budget(db, workspace_id=workspace_id)
        request_id = str(uuid.uuid4())
        started = time.monotonic()
        error = ""
        raw_text = ""
        model_uri_logged = f"FIXTURE:{self.settings.yandex_model_uri or 'yandexgpt-5.1'}"

        try:
            if use_fixture:
                raw_text = json.dumps(fixture_response, ensure_ascii=False)
            else:
                model_uri_logged = self.settings.yandex_model_uri
                raw_text = await self._call_real_model(prompt=prompt, stage=stage)
            parsed = result_model.model_validate_json(raw_text)
        except (ValidationError, json.JSONDecodeError, KeyError) as exc:
            error = f"invalid_json_or_schema: {exc}"
            parsed = None
        except httpx.HTTPError as exc:
            error = f"http_error: {exc}"
            parsed = None

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

        if parsed is None:
            raise AppError(
                "VALIDATION_ERROR",
                f"Ответ модели на этапе {stage!r} не прошёл проверку структуры: {error}",
            )
        return parsed

    async def check_relevance(self, db, *, workspace_id: uuid.UUID, news_item_id: uuid.UUID, title: str, theme: str) -> RelevanceResult:
        prompt = f"[{PROMPT_VERSION}:relevance] Тема потока: {theme}\nЗаголовок: {title}"
        fixture = {"is_relevant": True, "reasoning": "DEMO_FIXTURE: заголовок соответствует теме потока."}
        return await self._run_stage(
            db, workspace_id=workspace_id, news_item_id=news_item_id, stage="relevance",
            prompt=prompt, result_model=RelevanceResult, fixture_response=fixture,
        )

    async def build_fact_passport(self, db, *, workspace_id: uuid.UUID, news_item_id: uuid.UUID, source_fragment: str) -> FactPassportResult:
        prompt = f"[{PROMPT_VERSION}:facts] Извлеки факты из фрагмента, не придумывая дат/номеров дел:\n{source_fragment}"
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

    async def draft_independent_text(self, db, *, workspace_id: uuid.UUID, news_item_id: uuid.UUID, title: str, facts_text: str) -> DraftResult:
        prompt = f"[{PROMPT_VERSION}:draft] Напиши самостоятельный текст по фактам (не копируя источник):\nЗаголовок: {title}\nФакты: {facts_text}"
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
        prompt = f"[{PROMPT_VERSION}:critique] Найди неподтверждённые утверждения и текстовые совпадения:\nТекст: {text}\nФакты: {facts_text}"
        fixture = {"unconfirmed_claims": [], "distortions": [], "similarity_concerns": []}
        result = await self._run_stage(
            db, workspace_id=workspace_id, news_item_id=news_item_id, stage="critique",
            prompt=prompt, result_model=CritiqueResult, fixture_response=fixture,
        )
        return result
