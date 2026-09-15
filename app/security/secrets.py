"""Redact credentials and tokens from logs, errors, and HTML."""

from __future__ import annotations

import re
from typing import Any

_SECRET_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "session_secret",
    "client_secret",
    "authorization",
    "cookie",
    "csrf",
    "mysql_password",
    "lms_db_password",
    "admin_password",
    "google_client_secret",
    "bootstrap_token",
}

_ASSIGN_RE = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|bearer|session_secret|client_secret)\s*[=:]\s*([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)(bearer)\s+[A-Za-z0-9._\-+=/]+")
_QUERY_SECRET_RE = re.compile(r"(?i)([?&](?:password|passwd|secret|token|api[_-]?key|key|access_token)=)([^&\s]+)")


def is_secret_key(name: str | None) -> bool:
    text = (name or "").strip().lower().replace("-", "_")
    if text in _SECRET_KEYS:
        return True
    return any(part in text for part in ("password", "secret", "token", "api_key", "apikey"))


def mask_secret(value: str | None, *, visible: int = 0) -> str:
    if not value:
        return ""
    if visible <= 0 or len(value) <= 4:
        return "••••••••"
    return value[:visible] + "•" * max(8, len(value) - visible)


def redact_secrets(value: Any) -> Any:
    """Return a copy of *value* with credentials replaced by placeholders."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {
            key: (mask_secret(str(item)) if is_secret_key(str(key)) else redact_secrets(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        redacted = [redact_secrets(item) for item in value]
        return type(value)(redacted) if not isinstance(value, list) else redacted
    text = str(value)
    text = _QUERY_SECRET_RE.sub(r"\1***", text)
    text = _BEARER_RE.sub(r"\1 ***", text)
    text = _ASSIGN_RE.sub(r"\1=***", text)
    return text
