"""Password hashing with Argon2id and transparent PBKDF2 migration."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.exceptions import CyberScholarError

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 256
_PBKDF2_ITERATIONS = 120_000
_ARGON2_PREFIX = "argon2id$"
_PBKDF2_PREFIX = "pbkdf2_sha256$"

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerifyMismatchError

    _HASHER = PasswordHasher(
        time_cost=3,
        memory_cost=64 * 1024,
        parallelism=2,
        hash_len=32,
        salt_len=16,
    )
    ARGON2_AVAILABLE = True
except Exception:  # pragma: no cover - fallback when argon2-cffi is missing
    PasswordHasher = None  # type: ignore[misc, assignment]
    InvalidHashError = Exception  # type: ignore[misc, assignment]
    VerifyMismatchError = Exception  # type: ignore[misc, assignment]
    _HASHER = None  # type: ignore[assignment]
    ARGON2_AVAILABLE = False


class PasswordPolicyError(CyberScholarError):
    public_message = "That password does not meet the requirements."


def validate_password_policy(password: str, *, email: str = "") -> None:
    text = password or ""
    if len(text) < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")
    if len(text) > PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError("Password is too long.")
    if email and text.strip().lower() == email.strip().lower():
        raise PasswordPolicyError("Password must not match the email address.")


def hash_password(password: str) -> str:
    if ARGON2_AVAILABLE and _HASHER is not None:
        digest = _HASHER.hash(password)
        return f"{_ARGON2_PREFIX}{digest}"
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        _PBKDF2_ITERATIONS,
    ).hex()
    return f"{_PBKDF2_PREFIX}{_PBKDF2_ITERATIONS}${salt}${digest}"


def _verify_pbkdf2(password: str, stored: str) -> bool:
    if stored.count("$") != 3:
        return False
    scheme, iter_s, salt, digest = stored.split("$", 3)
    if scheme != "pbkdf2_sha256" or not iter_s.isdigit():
        return False
    check = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        int(iter_s),
    ).hex()
    return hmac.compare_digest(check, digest)


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    if stored.startswith(_ARGON2_PREFIX):
        if not ARGON2_AVAILABLE or _HASHER is None:
            return False
        digest = stored[len(_ARGON2_PREFIX) :]
        try:
            return bool(_HASHER.verify(digest, password))
        except (VerifyMismatchError, InvalidHashError, ValueError):
            return False
    if stored.startswith(_PBKDF2_PREFIX) or stored.startswith("pbkdf2_sha256"):
        return _verify_pbkdf2(password, stored)
    return False


def needs_rehash(stored: str | None) -> bool:
    if not stored:
        return True
    if not ARGON2_AVAILABLE or _HASHER is None:
        return False
    if stored.startswith(_PBKDF2_PREFIX) or stored.startswith("pbkdf2_sha256"):
        return True
    if stored.startswith(_ARGON2_PREFIX):
        digest = stored[len(_ARGON2_PREFIX) :]
        try:
            return bool(_HASHER.check_needs_rehash(digest))
        except Exception:
            return True
    return True
