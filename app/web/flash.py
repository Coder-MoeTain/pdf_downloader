"""Session-scoped flash messages. Never shared across users."""

from __future__ import annotations

from fastapi import Request

FLASH_KEY = "flash"
GIT_LOG_KEY = "git_log"
PM2_LOG_KEY = "pm2_log"


def set_flash(request: Request, text: str, level: str = "info") -> None:
    request.session[FLASH_KEY] = {"text": text, "level": level}


def pop_flash(request: Request) -> dict[str, str]:
    flash = request.session.pop(FLASH_KEY, None)
    if isinstance(flash, dict) and flash.get("text"):
        return {"text": str(flash.get("text") or ""), "level": str(flash.get("level") or "info")}
    return {"text": "", "level": "info"}


def set_job_log(request: Request, key: str, text: str) -> None:
    request.session[key] = text or ""


def get_job_log(request: Request, key: str) -> str:
    value = request.session.get(key)
    return value if isinstance(value, str) else ""
