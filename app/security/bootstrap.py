"""First-run bootstrap token. Never treated as HTML or a public API field."""

from __future__ import annotations

import hmac
import secrets
import threading

from app.utils.logger import get_logger

logger = get_logger("app.bootstrap")

_lock = threading.Lock()
_token: str | None = None
_consumed = False


def reset_bootstrap_state() -> None:
    """Test helper: drop any in-memory bootstrap token."""
    global _token, _consumed
    with _lock:
        _token = None
        _consumed = False


def bootstrap_token_active() -> bool:
    with _lock:
        return bool(_token) and not _consumed


def peek_bootstrap_token() -> str | None:
    """Return the current token for tests. Never call this from a public handler."""
    with _lock:
        return _token if not _consumed else None


def ensure_bootstrap_token(*, force: bool = False) -> str:
    """Create a one-time setup token and log it. The token is not returned to HTTP callers."""
    global _token, _consumed
    with _lock:
        if _token and not _consumed and not force:
            return _token
        _token = secrets.token_urlsafe(32)
        _consumed = False
    logger.warning(
        "FIRST-RUN SETUP TOKEN (one-time, not shown in the web UI): %s",
        _token,
    )
    logger.warning("Create the first administrator at /setup or run: python main.py create-admin")
    return _token


def verify_bootstrap_token(candidate: str | None) -> bool:
    with _lock:
        if _consumed or not _token:
            return False
        offered = (candidate or "").strip()
        if not offered:
            return False
        return hmac.compare_digest(_token, offered)


def consume_bootstrap_token() -> None:
    global _token, _consumed
    with _lock:
        _token = None
        _consumed = True
    logger.info("First-run bootstrap token invalidated after administrator creation.")
