import uuid

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.errors import AppError
from app.models import Membership, Session as SessionModel, User
from app.models.enums import Role
from app.security import csrf_token_valid
from app.services.auth_service import get_session_by_token


async def get_raw_session_token(request: Request) -> str:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        raise AppError("UNAUTHORIZED", "Требуется вход в систему.")
    return token


async def get_current_session(
    raw_token: str = Depends(get_raw_session_token),
    db: AsyncSession = Depends(get_db),
) -> SessionModel:
    session = await get_session_by_token(db, raw_token)
    if session is None:
        raise AppError("UNAUTHORIZED", "Сессия недействительна или истекла.")
    return session


async def get_current_user(
    session: SessionModel = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await db.get(User, session.user_id)
    if user is None:
        raise AppError("UNAUTHORIZED", "Пользователь не найден.")
    return user


async def require_csrf(
    request: Request,
    raw_token: str = Depends(get_raw_session_token),
) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    presented = request.headers.get("X-CSRF-Token", "")
    if not presented or not csrf_token_valid(raw_token, presented):
        raise AppError("FORBIDDEN", "Недействительный CSRF-токен.")


async def get_current_membership(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    x_workspace_id: str | None = Header(default=None),
) -> Membership:
    """MVP assumption (documented in ROADMAP): each user belongs to exactly one
    workspace created at registration. X-Workspace-Id lets a future multi-workspace
    user disambiguate; without it we fall back to the user's only/oldest membership."""
    stmt = select(Membership).where(Membership.user_id == user.id)
    if x_workspace_id:
        try:
            ws_id = uuid.UUID(x_workspace_id)
        except ValueError:
            raise AppError("VALIDATION_ERROR", "Некорректный X-Workspace-Id.")
        stmt = stmt.where(Membership.workspace_id == ws_id)
    stmt = stmt.order_by(Membership.created_at.asc())
    membership = await db.scalar(stmt)
    if membership is None:
        raise AppError("FORBIDDEN", "Нет доступа к рабочему пространству.")
    return membership


def require_role(*roles: Role):
    async def dependency(
        membership: Membership = Depends(get_current_membership),
        user: User = Depends(get_current_user),
    ) -> Membership:
        if user.is_platform_admin:
            return membership
        if membership.role not in roles:
            raise AppError("FORBIDDEN", "Недостаточно прав для этого действия.")
        return membership

    return dependency


async def require_platform_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_platform_admin:
        raise AppError("FORBIDDEN", "Требуются права platform_admin.")
    return user


async def require_active_access(
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
) -> Membership:
    from app.services.trial_service import has_mutating_access

    if not await has_mutating_access(db, membership.workspace_id):
        raise AppError("TRIAL_EXPIRED", "Демонстрационный доступ завершён. Материалы доступны только для чтения.")
    return membership
