import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from api.core.config import get_settings

_hasher = PasswordHasher()
# Verified against when the email is unknown so login timing doesn't reveal which emails exist.
_DUMMY_HASH = _hasher.hash("timing-equalizer-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: int, token_version: int) -> tuple[str, int]:
    s = get_settings()
    expires = timedelta(minutes=s.access_token_expire_minutes)
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "tv": token_version, "iat": now, "exp": now + expires}
    return jwt.encode(payload, s.jwt_secret_key.get_secret_value(), algorithm=s.jwt_algorithm), int(expires.total_seconds())


def decode_access_token(token: str) -> dict | None:
    s = get_settings()
    try:
        return jwt.decode(
            token,
            s.jwt_secret_key.get_secret_value(),
            algorithms=[s.jwt_algorithm],
            options={"require": ["sub", "exp", "tv"]},
        )
    except jwt.PyJWTError:
        return None


def new_reset_token() -> tuple[str, str]:
    """Returns (raw_token, sha256_hash). Only the hash is persisted."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_reset_token(raw)


def hash_reset_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_temp_password() -> str:
    return secrets.token_urlsafe(24)
