from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"

    database_url: str = "postgresql+asyncpg://pravovoy_potok:dev_local_only@localhost:5432/pravovoy_potok"
    redis_url: str = "redis://localhost:6379/0"

    session_cookie_name: str = "pp_session"
    csrf_cookie_name: str = "pp_csrf"
    csrf_secret: str = "dev-only-change-me"
    cookie_secure: bool = True
    cookie_domain: str | None = None
    # "lax" по умолчанию (доп. защита от CSRF на уровне куки, работает при общем домене
    # или обратном прокси на /api/*); "none" нужен, если frontend и API — разные домены
    # (например, два разных поддомена *.onrender.com) — тогда обязателен cookie_secure=true.
    cookie_samesite: str = "lax"
    session_ttl_hours: int = 24 * 14

    frontend_origin: str = "http://localhost:5173"

    trial_hours: int = 72
    trial_plan_code: str = "start"

    llm_provider: str = "yandexgpt_pro_5_1"
    yandex_folder_id: str = ""
    yandex_model_uri: str = ""
    yandex_api_key: str = ""
    llm_fixture_mode: bool = True
    llm_token_budget_per_workspace_day: int = 200_000
    llm_timeout_seconds: float = 30.0

    payment_provider: str = "not_configured"
    payment_webhook_secret: str = ""

    yandex_disk_enabled: bool = False

    log_level: str = "INFO"

    # SSRF / crawler limits
    fetch_max_bytes: int = 2_000_000
    fetch_timeout_seconds: float = 8.0
    fetch_max_redirects: int = 3

    rate_limit_register_per_hour: int = 10
    rate_limit_login_per_15min: int = 10
    rate_limit_reset_per_hour: int = 5
    rate_limit_scan_jobs_per_hour: int = 20

    def yandex_model_uri_matches_folder(self) -> bool:
        if not self.yandex_folder_id or not self.yandex_model_uri:
            return False
        expected = f"gpt://{self.yandex_folder_id}/yandexgpt-5.1"
        return self.yandex_model_uri == expected

    def llm_is_configured(self) -> bool:
        return bool(
            self.llm_provider == "yandexgpt_pro_5_1"
            and self.yandex_folder_id
            and self.yandex_api_key
            and self.yandex_model_uri_matches_folder()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
