from fastapi import Request
from fastapi.responses import JSONResponse

# Коды ошибок зафиксированы ТЗ §12. Не переименовывать без обновления ТЗ и клиента.
ERROR_STATUS = {
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "TRIAL_EXPIRED": 403,
    "PLAN_LIMIT": 403,
    "VALIDATION_ERROR": 422,
    "SOURCE_POLICY_REVIEW": 409,
    "ACCESS_LIMITED": 424,
    "OFFICIAL_SOURCE_MISSING": 409,
    "EDITOR_REVIEW_REQUIRED": 409,
    "ATTRIBUTION_REQUIRED": 409,
    "DUPLICATE": 409,
    "VERSION_CONFLICT": 409,
    "LLM_NOT_CONFIGURED": 503,
    "LLM_BUDGET_EXCEEDED": 429,
    "PAYMENT_NOT_CONFIGURED": 409,
    "EXPORT_NOT_SENT": 200,
    "NOT_FOUND": 404,
    "RATE_LIMITED": 429,
}


class AppError(Exception):
    def __init__(self, code: str, message: str, *, details: dict | None = None):
        if code not in ERROR_STATUS:
            raise ValueError(f"Unknown error code: {code}")
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

    @property
    def status_code(self) -> int:
        return ERROR_STATUS[self.code]


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )
