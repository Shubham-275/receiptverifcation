"""
Authentication primitives.

- Passwords: PBKDF2-SHA256 with a per-user random salt (stdlib only).
- Sessions: compact HMAC-SHA256 signed tokens stored in an HTTP-only cookie.
- Tokens carry the user id and an absolute expiry; the secret comes from
  ``AUTH_SECRET_KEY`` so sessions survive development reloads.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid

from app.config import get_settings

SESSION_COOKIE = "ri_session"

_PBKDF2_ALGORITHM = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 260_000
_USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


def normalize_username(value: str) -> str:
    """Trim and lowercase a login name."""
    return (value or "").strip().lower()


def validate_username(value: str) -> str:
    """Normalise and validate a username, raising ValueError when invalid."""
    username = normalize_username(value)
    if not _USERNAME_PATTERN.match(username):
        raise ValueError(
            "Username must be 3–64 characters, starting with a letter or number, "
            "using only letters, numbers, dots, dashes or underscores."
        )
    return username


def validate_password(value: str) -> None:
    if not isinstance(value, str) or len(value) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if len(value) > 128:
        raise ValueError("Password must be 128 characters or fewer.")


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    """Return a salted PBKDF2 hash in ``algorithm$iterations$salt$digest`` form."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return f"{_PBKDF2_ALGORITHM}${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


# A real hash used when the username does not exist, so failed logins take the
# same time whether or not the account exists.
_DUMMY_PASSWORD_HASH = hash_password("receipt-intelligence-dummy-password")


def verify_password(password: str, encoded: str | None) -> bool:
    """Constant-time password verification; always hashes when no hash exists."""
    stored = encoded or _DUMMY_PASSWORD_HASH
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$", 3)
        if algorithm != _PBKDF2_ALGORITHM:
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def create_session_token(user_id: uuid.UUID | str, *, ttl_hours: int | None = None) -> str:
    """Sign a compact session token for a user."""
    settings = get_settings()
    ttl_seconds = (
        ttl_hours if ttl_hours is not None else settings.auth_session_hours
    ) * 3600
    issued_at = int(time.time())
    payload = {
        "sub": str(user_id),
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
        "v": 1,
    }
    encoded = _b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(
        settings.auth_secret_key.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded}.{_b64encode(signature)}"


def verify_session_token(token: str | None) -> uuid.UUID | None:
    """Return the user id for a valid, unexpired token, otherwise ``None``."""
    if not token or "." not in token:
        return None
    encoded, signature = token.rsplit(".", 1)
    settings = get_settings()
    expected = hmac.new(
        settings.auth_secret_key.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        provided = _b64decode(signature)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(provided, expected):
        return None
    try:
        payload = json.loads(_b64decode(encoded))
        expires_at = int(payload.get("exp", 0))
        user_id = uuid.UUID(str(payload.get("sub")))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if expires_at < int(time.time()):
        return None
    return user_id


def session_max_age() -> int:
    """Cookie lifetime in seconds."""
    return get_settings().auth_session_hours * 3600
