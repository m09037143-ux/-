from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.cookies import clear_session_cookies, set_session_cookies
from app.db import get_db
from app.deps import get_current_user, get_raw_session_token, require_csrf
from app.models import Membership, User
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    MeResponse,
    MembershipOut,
    RegisterRequest,
    ResetPasswordRequest,
)
from app.services import auth_service
from app.services.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/register", response_model=MeResponse, status_code=201)
async def register(payload: RegisterRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    await enforce_rate_limit("register", _client_ip(request), settings.rate_limit_register_per_hour, 3600)
    user, workspace, session_token = await auth_service.register(
        db, name=payload.name, email=payload.email, password=payload.password
    )
    set_session_cookies(response, session_token=session_token)
    return MeResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        memberships=[MembershipOut(workspace_id=workspace.id, workspace_name=workspace.name, role="workspace_owner")],
    )


@router.post("/login", response_model=MeResponse)
async def login(payload: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    await enforce_rate_limit("login", _client_ip(request), settings.rate_limit_login_per_15min, 900)
    await enforce_rate_limit("login_email", payload.email.lower(), settings.rate_limit_login_per_15min, 900)
    user, session_token = await auth_service.login(db, email=payload.email, password=payload.password)
    set_session_cookies(response, session_token=session_token)
    return await _me_response(db, user)


@router.post("/logout", status_code=204, dependencies=[Depends(require_csrf)])
async def logout(
    response: Response,
    raw_token: str = Depends(get_raw_session_token),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    if raw_token:
        await auth_service.revoke_session(db, raw_token)
    clear_session_cookies(response)


@router.post("/forgot-password", status_code=202)
async def forgot_password(payload: ForgotPasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    await enforce_rate_limit("forgot_password", _client_ip(request), settings.rate_limit_reset_per_hour, 3600)
    raw_token = await auth_service.start_password_reset(db, email=payload.email)
    body = {"message": "Если такой email зарегистрирован, на него отправлена ссылка для восстановления."}
    body["email_delivery"] = "NOT_CONFIGURED"
    if settings.environment in ("development", "test") and raw_token:
        # Почтовый сервис не подключён (ТЗ §15) — для локальной разработки/тестов
        # отдаём токен напрямую вместо письма. В production это поле отсутствует.
        body["dev_reset_token"] = raw_token
    return body


@router.post("/reset-password", status_code=204)
async def reset_password(payload: ResetPasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    await enforce_rate_limit("reset_password", _client_ip(request), settings.rate_limit_reset_per_hour, 3600)
    await auth_service.reset_password(db, raw_token=payload.token, new_password=payload.new_password)


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _me_response(db, user)


async def _me_response(db: AsyncSession, user: User) -> MeResponse:
    memberships = (
        await db.scalars(
            select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at.asc())
        )
    ).all()
    out = []
    for m in memberships:
        await db.refresh(m, attribute_names=["workspace"])
        out.append(MembershipOut(workspace_id=m.workspace_id, workspace_name=m.workspace.name, role=m.role.value))
    return MeResponse(id=user.id, name=user.name, email=user.email, memberships=out)
