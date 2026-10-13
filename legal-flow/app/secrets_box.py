"""Шифрование секретов (API-ключей ИИ-сервисов), которые админ вводит в интерфейсе.

Fernet (AES-128-CBC + HMAC) с ключом из переменной SECRETS_ENCRYPTION_KEY. В базе лежит только
шифртекст; наружу (API, интерфейс, журнал) секрет не отдаётся никогда — только маска из последних
четырёх символов. Без SECRETS_ENCRYPTION_KEY сохранить ключ нельзя (понятная ошибка), а уже
работающая конфигурация из переменных окружения продолжает действовать.

Сгенерировать ключ шифрования (работает на любой машине с Python, без доп. библиотек):
    python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
Потеря этого ключа = потеря сохранённых в админке ключей (их придётся ввести заново), поэтому храните его
отдельно от базы (в переменных окружения сервера и в менеджере паролей)."""

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.errors import AppError

_HELP = (
    "Не задан или неверен SECRETS_ENCRYPTION_KEY — ключи ИИ нельзя сохранять в админке. Сгенерируйте ключ "
    "командой «python3 -c \"import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())\"» "
    "и добавьте его в переменные окружения сервера (deploy/.env или Render → Environment), затем перезапустите сервис."
)


def is_configured() -> bool:
    key = get_settings().secrets_encryption_key
    if not key:
        return False
    try:
        Fernet(key.encode("utf-8"))
    except (ValueError, TypeError):
        return False
    return True


def _fernet() -> Fernet:
    key = get_settings().secrets_encryption_key
    if not key:
        raise AppError("LLM_NOT_CONFIGURED", _HELP)
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise AppError("LLM_NOT_CONFIGURED", _HELP) from exc


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise AppError(
            "LLM_NOT_CONFIGURED",
            "Не удалось расшифровать сохранённый ключ: SECRETS_ENCRYPTION_KEY изменился. Введите ключ заново.",
        ) from exc


def hint(plain: str) -> str:
    """Маска для списка: последние 4 символа (по ним ключ узнают, восстановить его нельзя)."""
    tail = plain.strip()[-4:]
    return f"••••{tail}"
