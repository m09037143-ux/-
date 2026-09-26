from fastapi import Response

from app.config import get_settings
from app.security import new_csrf_token


def set_session_cookies(response: Response, *, session_token: str) -> None:
    settings = get_settings()
    csrf_token = new_csrf_token(session_token)
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        domain=settings.cookie_domain or None,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        domain=settings.cookie_domain or None,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name, path="/", domain=settings.cookie_domain or None)
    response.delete_cookie(settings.csrf_cookie_name, path="/", domain=settings.cookie_domain or None)
