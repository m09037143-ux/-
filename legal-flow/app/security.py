import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash

from app.config import get_settings

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(hashed: str, plain: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHash):
        return False


def needs_rehash(hashed: str) -> bool:
    return _hasher.check_needs_rehash(hashed)


def new_opaque_token(nbytes: int = 32) -> str:
    """Random token for session ids / reset tokens. Only a hash of it is stored server-side."""
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    """Server stores only this hash, never the raw token (mirrors password hashing intent
    for bearer-style secrets: a DB leak alone must not hand out valid sessions)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf_token(session_token: str) -> str:
    secret = get_settings().csrf_secret.encode("utf-8")
    return hmac.new(secret, session_token.encode("utf-8"), hashlib.sha256).hexdigest()


def csrf_token_valid(session_token: str, presented: str) -> bool:
    expected = new_csrf_token(session_token)
    return hmac.compare_digest(expected, presented)
