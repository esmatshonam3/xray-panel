"""Password hashing, JWT issuing, token hashing and symmetric encryption."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

# --------------------------------------------------------------------------- #
#  Password hashing (bcrypt, cost 12)
# --------------------------------------------------------------------------- #
_BCRYPT_ROUNDS = 12
_MAX_PASSWORD_BYTES = 72  # bcrypt hard limit


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")[:_MAX_PASSWORD_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        raw = password.encode("utf-8")[:_MAX_PASSWORD_BYTES]
        return bcrypt.checkpw(raw, password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
#  JWT
# --------------------------------------------------------------------------- #
def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_token(
    subject: str,
    *,
    token_type: str = "access",
    expires_delta: Optional[timedelta] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    if expires_delta is None:
        if token_type == "refresh":
            expires_delta = timedelta(days=settings.refresh_token_ttl_days)
        else:
            expires_delta = timedelta(minutes=settings.access_token_ttl_minutes)

    now = _now()
    payload: Dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": secrets.token_urlsafe(12),
        "iss": settings.app_name,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str, **extra: Any) -> str:
    return create_token(subject, token_type="access", extra=extra)


def create_refresh_token(subject: str, **extra: Any) -> str:
    return create_token(subject, token_type="refresh", extra=extra)


def decode_token(token: str, *, expected_type: Optional[str] = None) -> Dict[str, Any]:
    """Raises jwt.PyJWTError subclasses on failure."""
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.app_name,
        options={"require": ["exp", "sub", "type"]},
    )
    if expected_type and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"expected {expected_type} token")
    return payload


# --------------------------------------------------------------------------- #
#  Opaque tokens (API keys, subscription tokens, node tokens)
# --------------------------------------------------------------------------- #
def generate_token(nbytes: int = 32, prefix: str = "") -> str:
    return f"{prefix}{secrets.token_urlsafe(nbytes)}"


def hash_token(token: str) -> str:
    """API keys are stored as SHA-256 digests, never in plaintext."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def generate_sub_token() -> str:
    return secrets.token_urlsafe(24)


def generate_uuid() -> str:
    import uuid

    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
#  Symmetric encryption for secrets at rest (node tokens, reality keys)
# --------------------------------------------------------------------------- #
def _fernet() -> Fernet:
    key = (settings.encryption_key or "").strip()
    if key:
        try:
            return Fernet(key.encode("ascii"))
        except (ValueError, TypeError):
            pass
    # Deterministic derivation so restarts keep decrypting existing rows.
    digest = hashlib.sha256(f"xpanel:{settings.secret_key}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def mask(value: Optional[str], keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 8}{value[-keep:]}"


# --------------------------------------------------------------------------- #
#  TOTP (optional 2FA for admins)
# --------------------------------------------------------------------------- #
def new_totp_secret() -> str:
    import pyotp

    return pyotp.random_base32()


def totp_uri(secret: str, account: str) -> str:
    import pyotp

    return pyotp.totp.TOTP(secret).provisioning_uri(name=account, issuer_name=settings.app_name)


def verify_totp(secret: str, code: str, valid_window: int = 1) -> bool:
    import pyotp

    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=valid_window)
