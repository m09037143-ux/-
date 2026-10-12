import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import AppError
from app.models import ApiKey
from app.services.activity_service import log_activity

KEY_PREFIX = "pp_live_"
_LAST_USED_REFRESH = timedelta(minutes=5)  # не пишем в БД на каждый запрос


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def display_prefix(raw: str) -> str:
    """Начало ключа для списка в кабинете (по нему ключ узнают, но восстановить его нельзя)."""
    return raw[: len(KEY_PREFIX) + 4]


async def create_key(db: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID, name: str) -> tuple[ApiKey, str]:
    active = await db.scalar(
        select(func.count()).select_from(ApiKey).where(ApiKey.workspace_id == workspace_id, ApiKey.revoked_at.is_(None))
    )
    limit = get_settings().max_api_keys_per_workspace
    if active >= limit:
        raise AppError("PLAN_LIMIT", f"Не более {limit} действующих API-ключей: отзовите ненужные.")
    raw = generate_key()
    key = ApiKey(
        workspace_id=workspace_id, name=name.strip(), key_prefix=display_prefix(raw), key_hash=hash_key(raw),
        created_by=user_id, created_at=datetime.now(timezone.utc),
    )
    db.add(key)
    await db.commit()
    await log_activity(
        db, workspace_id=workspace_id, action="api_key_created", actor_user_id=user_id,
        details={"key_id": str(key.id), "name": key.name},
    )
    return key, raw


async def revoke_key(db: AsyncSession, *, workspace_id: uuid.UUID, key_id: uuid.UUID, user_id: uuid.UUID) -> None:
    key = await db.get(ApiKey, key_id)
    if key is None or key.workspace_id != workspace_id:
        raise AppError("NOT_FOUND", "Ключ не найден.")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        await db.commit()
        await log_activity(
            db, workspace_id=workspace_id, action="api_key_revoked", actor_user_id=user_id,
            details={"key_id": str(key.id), "name": key.name},
        )


async def authenticate(db: AsyncSession, raw: str) -> ApiKey | None:
    if not raw.startswith(KEY_PREFIX):
        return None
    key = await db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(raw), ApiKey.revoked_at.is_(None)))
    if key is None:
        return None
    now = datetime.now(timezone.utc)
    if key.last_used_at is None or now - key.last_used_at > _LAST_USED_REFRESH:
        key.last_used_at = now
        await db.commit()
    return key
