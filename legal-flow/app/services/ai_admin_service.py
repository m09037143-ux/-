"""Управление ИИ-подключениями и моделями по этапам из админки платформы.

Ключи принимаются один раз, шифруются (app/secrets_box.py) и больше не отдаются: наружу — только маска из
последних четырёх символов. В журнал активности пишутся названия и модели, но никогда ключи."""

import time
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import secrets_box
from app.config import get_settings
from app.errors import AppError
from app.models import AiConnection, AiStageModel
from app.providers import llm_targets
from app.providers.llm_targets import ADMIN_STAGES, YANDEX_MODEL_SUGGESTIONS, build_target, call_model, validate_base_url
from app.schemas.admin_ai import (
    AiOverviewOut,
    ConnectionCreate,
    ConnectionOut,
    ConnectionUpdate,
    EffectiveOut,
    StageOut,
    StagesUpdate,
    TestResultOut,
    YandexKeyRequest,
)
from app.services.activity_service import log_activity

_STAGE_TITLES = {"relevance": "Отбор по теме", "facts": "Паспорт фактов", "draft": "Черновик", "fallback": "Запасная модель"}


def connection_out(c: AiConnection) -> ConnectionOut:
    return ConnectionOut(
        id=c.id, name=c.name, kind=c.kind, base_url=c.base_url, folder_id=c.folder_id, key_hint=c.key_hint,
        last_check_at=c.last_check_at, last_check_ok=c.last_check_ok, last_check_note=c.last_check_note or "",
    )


def _env_effective(stage: str) -> EffectiveOut:
    """Что работает на этапе, если в админке ничего не задано: настройки сервера."""
    s = get_settings()
    if stage == "fallback":
        model = (s.llm_fallback_model or "").strip()
        return EffectiveOut(stage=stage, source="env" if model else "none", model=model)
    if s.llm_is_configured():
        return EffectiveOut(stage=stage, source="env", model=s.yandex_model_uri.rsplit("/", 1)[-1])
    if s.llm_fixture_mode:
        return EffectiveOut(stage=stage, source="fixture", model="демо (без реального ИИ)")
    return EffectiveOut(stage=stage, source="not_configured")


async def overview(db: AsyncSession) -> AiOverviewOut:
    connections = (await db.scalars(select(AiConnection).order_by(AiConnection.created_at))).all()
    names = {c.id: c.name for c in connections}
    rows = {r.stage: r for r in (await db.scalars(select(AiStageModel))).all()}
    stages, effective = [], []
    for stage in ADMIN_STAGES:
        row = rows.get(stage)
        if row is not None and row.connection_id in names:
            stages.append(StageOut(stage=stage, connection_id=row.connection_id, connection_name=names[row.connection_id], model=row.model))
            effective.append(EffectiveOut(stage=stage, source="admin", model=row.model, connection_name=names[row.connection_id]))
        else:
            stages.append(StageOut(stage=stage))
            effective.append(_env_effective(stage))
    return AiOverviewOut(
        connections=[connection_out(c) for c in connections], stages=stages, effective=effective,
        encryption_configured=secrets_box.is_configured(), model_suggestions=YANDEX_MODEL_SUGGESTIONS,
        default_folder_id=next((c.folder_id for c in connections if c.kind == "yandex" and c.folder_id), "") or get_settings().yandex_folder_id,
    )


async def _prepare_fields(kind: str, *, folder_id: str, base_url: str) -> tuple[str, str]:
    if kind == "yandex":
        if not folder_id:
            raise AppError("VALIDATION_ERROR", "Для Яндекс Облака укажите идентификатор каталога.")
        return folder_id, ""
    if not base_url:
        raise AppError("VALIDATION_ERROR", "Для другого сервиса укажите адрес API (например, https://api.example.com/v1).")
    return "", await validate_base_url(base_url)


async def create_connection(db: AsyncSession, *, payload: ConnectionCreate, user_id: uuid.UUID, workspace_id: uuid.UUID) -> AiConnection:
    folder_id, base_url = await _prepare_fields(payload.kind, folder_id=payload.folder_id, base_url=payload.base_url)
    encrypted = secrets_box.encrypt(payload.api_key)  # без SECRETS_ENCRYPTION_KEY — понятная ошибка, ничего не сохраняется
    conn = AiConnection(
        name=payload.name.strip(), kind=payload.kind, base_url=base_url, folder_id=folder_id,
        api_key_encrypted=encrypted, key_hint=secrets_box.hint(payload.api_key), created_by=user_id,
    )
    db.add(conn)
    await db.commit()
    await log_activity(
        db, workspace_id=workspace_id, actor_user_id=user_id, action="ai_connection_created",
        details={"connection_id": str(conn.id), "name": conn.name, "kind": conn.kind},
    )
    return conn


async def _get(db: AsyncSession, connection_id: uuid.UUID) -> AiConnection:
    conn = await db.get(AiConnection, connection_id)
    if conn is None:
        raise AppError("NOT_FOUND", "Подключение не найдено.")
    return conn


async def update_connection(
    db: AsyncSession, *, connection_id: uuid.UUID, payload: ConnectionUpdate, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> AiConnection:
    conn = await _get(db, connection_id)
    if payload.name is not None:
        conn.name = payload.name.strip()
    folder_id = payload.folder_id if payload.folder_id is not None else conn.folder_id
    base_url = payload.base_url if payload.base_url is not None else conn.base_url
    conn.folder_id, conn.base_url = await _prepare_fields(conn.kind, folder_id=folder_id, base_url=base_url)
    replaced_key = payload.api_key is not None
    if replaced_key:
        conn.api_key_encrypted = secrets_box.encrypt(payload.api_key)
        conn.key_hint = secrets_box.hint(payload.api_key)
    conn.last_check_at, conn.last_check_ok, conn.last_check_note = None, None, ""  # старая проверка уже не про эти данные
    await db.commit()
    llm_targets.invalidate_cache()
    await log_activity(
        db, workspace_id=workspace_id, actor_user_id=user_id, action="ai_connection_updated",
        details={"connection_id": str(conn.id), "name": conn.name, "key_replaced": replaced_key},
    )
    return conn


async def delete_connection(db: AsyncSession, *, connection_id: uuid.UUID, user_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
    conn = await _get(db, connection_id)
    used = (await db.scalars(select(AiStageModel).where(AiStageModel.connection_id == conn.id))).all()
    if used:
        titles = ", ".join(_STAGE_TITLES.get(r.stage, r.stage) for r in used)
        raise AppError("VALIDATION_ERROR", f"Подключение используется этапами ({titles}) — сначала переназначьте или сбросьте их.")
    name = conn.name
    await db.delete(conn)
    await db.commit()
    await log_activity(
        db, workspace_id=workspace_id, actor_user_id=user_id, action="ai_connection_deleted",
        details={"connection_id": str(connection_id), "name": name},
    )


async def set_stages(db: AsyncSession, *, payload: StagesUpdate, user_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
    unknown = set(payload.stages) - set(ADMIN_STAGES)
    if unknown:
        raise AppError("VALIDATION_ERROR", f"Неизвестные этапы: {sorted(unknown)}")
    changes: dict[str, str | None] = {}
    for stage, spec in payload.stages.items():
        row = await db.scalar(select(AiStageModel).where(AiStageModel.stage == stage))
        if spec is None:
            if row is not None:
                await db.delete(row)
            changes[stage] = None
            continue
        conn = await db.get(AiConnection, spec.connection_id)
        if conn is None:
            raise AppError("VALIDATION_ERROR", f"{_STAGE_TITLES[stage]}: подключение не найдено.")
        if row is None:
            db.add(AiStageModel(stage=stage, connection_id=conn.id, model=spec.model, updated_by=user_id))
        else:
            row.connection_id, row.model, row.updated_by = conn.id, spec.model, user_id
        changes[stage] = f"{conn.name} · {spec.model}"
    await db.commit()
    llm_targets.invalidate_cache()
    await log_activity(
        db, workspace_id=workspace_id, actor_user_id=user_id, action="ai_stages_updated",
        details={"changes": changes},
    )


async def probe_target(target) -> tuple[bool, str, int]:
    """Крошечный реальный запрос к модели: (работает ли, пояснение для человека, задержка в мс)."""
    started = time.monotonic()
    ok, note = False, ""
    try:
        text = await call_model(target, prompt="Ответь одним словом: ок", timeout=25.0, max_tokens=24, temperature=0.0)
        ok = bool(text.strip())
        note = "Модель ответила — подключение работает." if ok else "Сервис ответил пустым текстом."
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        hint = {
            400: "неверное имя модели или параметры запроса",
            401: "ключ не принят",
            403: "у ключа нет прав на эту модель или каталог",
            404: "неверный адрес API или имя модели",
            429: "превышен лимит запросов или не хватает средств",
        }.get(code, "ошибка на стороне сервиса")
        note = f"HTTP {code}: {hint}."
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        note = f"Сервис недоступен ({type(exc).__name__})."
    except (KeyError, IndexError, ValueError, TypeError):
        note = "Сервис ответил в неожиданном формате — проверьте адрес API (нужен OpenAI-совместимый /chat/completions)."
    return ok, note, int((time.monotonic() - started) * 1000)


MAIN_YANDEX_NAME = "Яндекс Облако (основной)"


def _env_main_model() -> str:
    s = get_settings()
    return s.yandex_model_uri.rsplit("/", 1)[-1] if s.llm_is_configured() else "yandexgpt-5.1"


async def replace_yandex_key(
    db: AsyncSession, *, payload: YandexKeyRequest, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> TestResultOut:
    """Замена ключа YandexGPT одним действием. Сначала новый ключ проверяется реальным запросом — если не работает,
    ничего не меняется. Затем ключ сохраняется (зашифрованным) в основном подключении Яндекса, а этапы без собственной
    настройки (отбор, факты, черновик и — если задана — запасная модель) переводятся на него, так что старый ключ из
    настроек сервера больше не нужен. Этапы, уже назначенные на другие подключения, не трогаются."""
    settings = get_settings()
    main = await db.scalar(select(AiConnection).where(AiConnection.kind == "yandex").order_by(AiConnection.created_at))
    folder_id = payload.folder_id or (main.folder_id if main else "") or settings.yandex_folder_id
    if not folder_id:
        raise AppError("VALIDATION_ERROR", "Укажите идентификатор каталога Яндекс Облака.")
    rows = {r.stage: r for r in (await db.scalars(select(AiStageModel))).all()}
    probe_model = (rows["draft"].model if "draft" in rows and rows["draft"].model.startswith("yandexgpt") else None) or _env_main_model()
    target = build_target(kind="yandex", model=probe_model, api_key=payload.api_key, folder_id=folder_id, source="admin", connection_name=MAIN_YANDEX_NAME)
    ok, note, latency = await probe_target(target)
    if not ok:
        raise AppError("VALIDATION_ERROR", f"Новый ключ не прошёл проверку ({probe_model}): {note} Ничего не изменено, прежний ключ продолжает работать.")
    encrypted = secrets_box.encrypt(payload.api_key)  # без SECRETS_ENCRYPTION_KEY — понятная ошибка, ничего не сохраняется
    now = datetime.now(timezone.utc)
    if main is None:
        main = AiConnection(name=MAIN_YANDEX_NAME, kind="yandex", base_url="", folder_id=folder_id, api_key_encrypted=encrypted,
                            key_hint=secrets_box.hint(payload.api_key), created_by=user_id)
        db.add(main)
        await db.flush()
    else:
        main.folder_id, main.api_key_encrypted, main.key_hint = folder_id, encrypted, secrets_box.hint(payload.api_key)
    main.last_check_at, main.last_check_ok, main.last_check_note = now, True, f"{probe_model}: {note}"
    wanted = {"relevance": _env_main_model(), "facts": _env_main_model(), "draft": _env_main_model()}
    if (settings.llm_fallback_model or "").strip():
        wanted["fallback"] = settings.llm_fallback_model.strip()
    assigned = []
    for stage, model in wanted.items():
        if stage in rows:
            continue  # у этапа уже есть своя настройка из админки — не перебиваем
        db.add(AiStageModel(stage=stage, connection_id=main.id, model=model, updated_by=user_id))
        assigned.append(stage)
    await db.commit()
    llm_targets.invalidate_cache()
    await log_activity(
        db, workspace_id=workspace_id, actor_user_id=user_id, action="ai_yandex_key_replaced",
        details={"connection_id": str(main.id), "folder_id": folder_id, "assigned_stages": assigned, "model_checked": probe_model},
    )
    return TestResultOut(ok=True, latency_ms=latency, note=f"Новый ключ работает ({probe_model}) и применён." + (
        f" Этапы переведены на него: {', '.join(_STAGE_TITLES[s] for s in assigned)}." if assigned else ""))


async def test_connection(db: AsyncSession, *, connection_id: uuid.UUID, model: str, user_id: uuid.UUID, workspace_id: uuid.UUID) -> TestResultOut:
    """Крошечный реальный запрос выбранной модели через подключение: проверяет ключ, адрес и имя модели."""
    conn = await _get(db, connection_id)
    key = secrets_box.decrypt(conn.api_key_encrypted)
    base_url = await validate_base_url(conn.base_url) if conn.kind == "openai_compatible" else ""
    target = build_target(kind=conn.kind, model=model, api_key=key, folder_id=conn.folder_id, base_url=base_url, source="admin", connection_name=conn.name)
    ok, note, latency = await probe_target(target)
    conn.last_check_at, conn.last_check_ok, conn.last_check_note = datetime.now(timezone.utc), ok, f"{model}: {note}"
    await db.commit()
    await log_activity(
        db, workspace_id=workspace_id, actor_user_id=user_id, action="ai_connection_tested",
        details={"connection_id": str(conn.id), "name": conn.name, "model": model, "ok": ok},
    )
    return TestResultOut(ok=ok, latency_ms=latency, note=note)
