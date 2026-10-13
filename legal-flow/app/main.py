import logging

from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.errors import AppError, app_error_handler, request_validation_error_handler
from app.routers import access, activity, admin, admin_ai, ai, api_keys, auth, billing, exports, feed, flows, news


class _NoCacheStaticFiles(StaticFiles):
    """Frontend отдаётся с Cache-Control: no-cache — браузер каждый раз сверяется с сервером по ETag
    (без изменений — ответ 304 без загрузки файла), поэтому после деплоя пользователи сразу получают
    новую версию, а не закэшированную старую (раньше помогал только Ctrl+F5)."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(title="Правовой Поток — API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Workspace-Id", "Idempotency-Key"],
    )

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)

    app.include_router(auth.router)
    app.include_router(access.router)
    app.include_router(billing.router)
    app.include_router(flows.router)
    app.include_router(news.router)
    app.include_router(activity.router)
    app.include_router(ai.router)
    app.include_router(exports.router)
    app.include_router(api_keys.router)
    app.include_router(feed.router)
    app.include_router(admin.router)
    app.include_router(admin_ai.router)

    @app.get("/health/live")
    async def health_live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def health_ready():
        from app.db import get_engine
        from app.redis_client import get_redis
        from sqlalchemy import text

        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
            await get_redis().ping()
        except Exception as exc:  # noqa: BLE001 -- readiness probe must report, not crash
            return {"status": "error", "detail": str(exc)}
        return {"status": "ok"}

    # Отдаём frontend прямо из backend-процесса — так весь сайт живёт на одном
    # домене (не нужны CORS/куки между сайтами, отдельный статический сервер
    # и синхронизация двух адресов при деплое). Смонтировано ПОСЛЕДНИМ, чтобы
    # API-роуты (/api/v1/..., /health/...) matched первыми.
    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    if frontend_dir.is_dir():
        app.mount("/", _NoCacheStaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app
