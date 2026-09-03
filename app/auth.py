"""Google sign-in helpers and access control."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_runtime_config
from app.database.models import User
from app.utils.time import utc_now

PUBLIC_PATHS = {"/login", "/auth/google", "/auth/google/callback", "/logout"}
ADMIN_PREFIXES = ("/sources", "/settings", "/api/sources")

_oauth = None
_oauth_key: tuple[str, str] | None = None


def google_login_enabled() -> bool:
    cfg = get_runtime_config()
    return bool(cfg.env.google_client_id.strip() and cfg.env.google_client_secret.strip())


def session_secret() -> str:
    return get_runtime_config().env.session_secret or "change-me-in-production-please-use-a-long-random-string"


def admin_emails() -> set[str]:
    cfg = get_runtime_config()
    emails = {
        part.strip().lower()
        for part in (cfg.env.google_admin_emails or "").split(",")
        if part.strip()
    }
    contact = (cfg.env.contact_email or "").strip().lower()
    if contact and "@" in contact and "example.com" not in contact:
        emails.add(contact)
    return emails


def is_admin_email(email: str | None) -> bool:
    if not email:
        return False
    return email.strip().lower() in admin_emails()


def current_user(request: Request) -> dict[str, Any] | None:
    user = request.session.get("user")
    return user if isinstance(user, dict) else None


def user_is_admin(request: Request) -> bool:
    if not google_login_enabled():
        return True
    user = current_user(request)
    return bool(user and user.get("is_admin"))


def is_public_path(path: str) -> bool:
    if path.startswith("/static"):
        return True
    return path in PUBLIC_PATHS


def is_admin_path(path: str) -> bool:
    return path == "/sources" or path.startswith(ADMIN_PREFIXES)


def get_oauth():
    global _oauth, _oauth_key
    from authlib.integrations.starlette_client import OAuth

    cfg = get_runtime_config()
    key = (cfg.env.google_client_id.strip(), cfg.env.google_client_secret.strip())
    if _oauth is None or _oauth_key != key:
        oauth = OAuth()
        oauth.register(
            name="google",
            client_id=key[0],
            client_secret=key[1],
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
        _oauth = oauth
        _oauth_key = key
    return _oauth


def user_to_session(row: User) -> dict[str, Any]:
    return {
        "id": row.id,
        "email": row.email,
        "name": row.name or row.email,
        "picture": row.picture or "",
        "is_admin": bool(row.is_admin),
    }


def upsert_google_user(
    session: Session,
    *,
    google_id: str,
    email: str,
    name: str = "",
    picture: str | None = None,
) -> User:
    email = (email or "").strip().lower()
    google_id = (google_id or "").strip()
    row = session.scalar(select(User).where(User.google_id == google_id))
    if row is None:
        row = session.scalar(select(User).where(func.lower(User.email) == email))
    admin = is_admin_email(email)
    if row is None:
        if not admin_emails() and session.scalar(select(func.count(User.id))) == 0:
            admin = True
        row = User(
            google_id=google_id,
            email=email,
            name=name or email,
            picture=picture,
            is_admin=admin,
        )
        session.add(row)
    else:
        row.google_id = google_id or row.google_id
        row.email = email or row.email
        if name:
            row.name = name
        if picture:
            row.picture = picture
        if admin:
            row.is_admin = True
    row.last_login_at = utc_now()
    session.flush()
    return row


def safe_next_path(value: str | None) -> str:
    text = (value or "").strip()
    if text.startswith("/") and not text.startswith("//"):
        return text
    return "/"
