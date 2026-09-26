import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import AppError
from app.models import Membership, PasswordResetToken, Session as SessionModel, Trial, User, Workspace
from app.models.enums import Role
from app.security import (
    hash_password,
    new_opaque_token,
    token_hash,
    verify_password,
)

async def register(db: AsyncSession, *, name: str, email: str, password: str) -> tuple[User, Workspace, str]:
    email_norm = email.strip().lower()
    existing = await db.scalar(select(User).where(User.email == email_norm))
    if existing is not None:
        raise AppError("VALIDATION_ERROR", "Пользователь с таким email уже зарегистрирован.")

    settings = get_settings()
    now = datetime.now(timezone.utc)

    user = User(name=name.strip(), email=email_norm, password_hash=hash_password(password))
    db.add(user)
    await db.flush()

    workspace = Workspace(name=f"Рабочее пространство {name.strip()}")
    db.add(workspace)
    await db.flush()

    db.add(Membership(workspace_id=workspace.id, user_id=user.id, role=Role.workspace_owner))

    # Записывается один раз и никогда не переписывается (ТЗ §6) — уникальный индекс
    # на workspace_id физически исключает повторную запись даже при гонке запросов.
    db.add(
        Trial(
            workspace_id=workspace.id,
            started_at=now,
            ends_at=now + timedelta(hours=settings.trial_hours),
        )
    )

    session_token = await _create_session(db, user_id=user.id)
    await db.commit()
    return user, workspace, session_token


async def login(db: AsyncSession, *, email: str, password: str) -> tuple[User, str]:
    email_norm = email.strip().lower()
    user = await db.scalar(select(User).where(User.email == email_norm))
    if user is None or not verify_password(user.password_hash, password):
        raise AppError("UNAUTHORIZED", "Неверный email или пароль.")
    session_token = await _create_session(db, user_id=user.id)
    await db.commit()
    return user, session_token


async def _create_session(db: AsyncSession, *, user_id: uuid.UUID) -> str:
    settings = get_settings()
    raw_token = new_opaque_token()
    now = datetime.now(timezone.utc)
    db.add(
        SessionModel(
            user_id=user_id,
            token_hash=token_hash(raw_token),
            created_at=now,
            expires_at=now + timedelta(hours=settings.session_ttl_hours),
        )
    )
    return raw_token


async def get_session_by_token(db: AsyncSession, raw_token: str) -> SessionModel | None:
    row = await db.scalar(select(SessionModel).where(SessionModel.token_hash == token_hash(raw_token)))
    if row is None:
        return None
    now = datetime.now(timezone.utc)
    if row.revoked_at is not None or row.expires_at < now:
        return None
    return row


async def revoke_session(db: AsyncSession, raw_token: str) -> None:
    row = await db.scalar(select(SessionModel).where(SessionModel.token_hash == token_hash(raw_token)))
    if row is not None:
        row.revoked_at = datetime.now(timezone.utc)
        await db.commit()


async def revoke_all_sessions_for_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    rows = (await db.scalars(select(SessionModel).where(SessionModel.user_id == user_id))).all()
    for row in rows:
        if row.revoked_at is None:
            row.revoked_at = now
    await db.commit()


async def start_password_reset(db: AsyncSession, *, email: str) -> str | None:
    """Always looks like it succeeded to the caller (no user enumeration). Returns the
    raw token only for callers that need it (dev/test), never over the wire in prod."""
    email_norm = email.strip().lower()
    user = await db.scalar(select(User).where(User.email == email_norm))
    if user is None:
        return None
    raw_token = new_opaque_token()
    now = datetime.now(timezone.utc)
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash(raw_token),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )
    await db.commit()
    return raw_token


async def reset_password(db: AsyncSession, *, raw_token: str, new_password: str) -> None:
    row = await db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash(raw_token))
    )
    now = datetime.now(timezone.utc)
    if row is None or row.used_at is not None or row.expires_at < now:
        raise AppError("VALIDATION_ERROR", "Ссылка для восстановления недействительна или устарела.")
    user = await db.get(User, row.user_id)
    user.password_hash = hash_password(new_password)
    row.used_at = now
    await db.commit()
    await revoke_all_sessions_for_user(db, user.id)
