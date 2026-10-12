import re
from pathlib import Path

import pytest

from app.config import Settings

yaml = pytest.importorskip("yaml")

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
_DEPLOY_ONLY_VARS = {"COMPOSE_FILE", "DOMAIN", "POSTGRES_PASSWORD"}  # нужны compose, но не приложению


def _compose() -> dict:
    return yaml.safe_load((DEPLOY / "docker-compose.prod.yml").read_text(encoding="utf-8"))


def _example_vars() -> dict[str, str]:
    result = {}
    for line in (DEPLOY / ".env.production.example").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def test_compose_has_the_five_services_and_only_caddy_is_published():
    services = _compose()["services"]
    assert set(services) == {"caddy", "app", "worker", "db", "redis"}
    published = {name for name, spec in services.items() if spec.get("ports")}
    assert published == {"caddy"}  # база, Redis и приложение снаружи недоступны
    assert {"80:80", "443:443"} <= set(services["caddy"]["ports"])


def test_app_and_worker_use_internal_hosts_trust_proxy_headers_and_restart():
    services = _compose()["services"]
    for name in ("app", "worker"):
        env = services[name]["environment"]
        assert "@db:5432/" in env["DATABASE_URL"] and "${POSTGRES_PASSWORD}" in env["DATABASE_URL"], name
        assert env["REDIS_URL"].startswith("redis://redis:"), name
        assert env["FORWARDED_ALLOW_IPS"] == "*", name  # иначе все клиенты для лимитов — один адрес (Caddy)
        assert env["ENVIRONMENT"] == "production" and services[name]["restart"] == "unless-stopped"
    assert services["app"]["command"] == ["./docker-entrypoint.sh"]  # миграции перед стартом
    assert services["worker"]["command"] == ["python", "-m", "app.worker.runner"]
    assert services["worker"]["depends_on"]["app"]["condition"] == "service_healthy"
    assert "healthcheck" in services["app"] and "/health/live" in str(services["app"]["healthcheck"]["test"])


def test_data_services_have_healthchecks_and_a_persistent_database_volume():
    compose = _compose()
    assert "healthcheck" in compose["services"]["db"] and "healthcheck" in compose["services"]["redis"]
    assert "pp_db_data" in compose["volumes"] and "caddy_data" in compose["volumes"]
    assert any("pp_db_data" in v for v in compose["services"]["db"]["volumes"])


def test_every_compose_variable_is_defined_in_the_env_template():
    text = (DEPLOY / "docker-compose.prod.yml").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\{([A-Z_]+)\}", text))
    assert used and used <= set(_example_vars()), used - set(_example_vars())


def test_env_template_variables_match_real_application_settings():
    fields = {name.upper() for name in Settings.model_fields}
    unknown = set(_example_vars()) - _DEPLOY_ONLY_VARS - fields
    # FRONTEND_ORIGIN и пр. — настройки приложения; опечатка в имени переменной здесь ловится
    assert not unknown, f"в шаблоне есть переменные, которых нет в Settings: {sorted(unknown)}"


def test_env_template_has_only_placeholders_for_secrets_and_production_modes():
    env = _example_vars()
    for key in ("POSTGRES_PASSWORD", "CSRF_SECRET", "YANDEX_FOLDER_ID", "YANDEX_API_KEY", "YANDEX_MODEL_URI"):
        assert "СГЕНЕРИРУЙ" in env[key] or "ВАШ" in env[key], key
    assert env["DOMAIN"] == "pravpotok.ru" and env["FRONTEND_ORIGIN"] == "https://pravpotok.ru"
    assert env["LLM_FIXTURE_MODE"] == "false" and env["SOURCE_FIXTURE_MODE"] == "false" and env["COOKIE_SECURE"] == "true"
    assert env["LLM_FALLBACK_MODEL"] == "deepseek-v4-flash"


def test_caddyfile_proxies_to_the_app_and_redirects_www():
    text = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "{$DOMAIN} {" in text and "reverse_proxy app:8000" in text
    assert "www.{$DOMAIN}" in text and "redir https://{$DOMAIN}{uri} permanent" in text
    assert "Strict-Transport-Security" in text


@pytest.mark.parametrize("script", ["backup.sh", "restore.sh", "update.sh"])
def test_shell_scripts_are_posix_lf_and_fail_fast(script):
    raw = (DEPLOY / script).read_bytes()
    assert b"\r" not in raw, "скрипт должен быть с переводами строк LF"
    text = raw.decode("utf-8")
    assert text.startswith("#!/bin/sh\n") and "set -eu" in text


def test_restore_script_asks_for_confirmation_before_wiping_the_database():
    text = (DEPLOY / "restore.sh").read_text(encoding="utf-8")
    assert text.index("read -r answer") < text.index("DROP DATABASE")
    assert 'if [ -z "$dump" ]' in text


def test_readme_covers_dns_and_the_render_migration():
    text = (DEPLOY / "README.md").read_text(encoding="utf-8")
    for needle in ("pravpotok.ru", "DNS", "pg_dump", "restore.sh", "Резервные копии", "FORWARDED_ALLOW_IPS", "Deploy keys"):
        assert needle in text, needle
    assert "vDcWpKsM" not in text and "mcI49a88" not in text  # присланные пароли в репозиторий не попадают
