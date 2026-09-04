"""Sign-in helpers, local passwords, and user/admin roles."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_runtime_config
from app.database.models import User
from app.utils.time import utc_now

PUBLIC_PATHS = {"/login", "/auth/google", "/auth/google/callback", "/logout"}
ADMIN_PREFIXES = ("/sources", "/settings", "/api/sources", "/api/activity", "/account/users")
ROLE_USER = "user"
ROLE_ADMIN = "admin"
PASSWORD_MIN_LENGTH = 8
DEFAULT_ADMIN_EMAIL = "admin@localhost"
DEFAULT_ADMIN_PASSWORD = "Admin@123"
DEFAULT_ADMIN_NAME = "Administrator"
_PBKDF2_ITERATIONS = 120_000

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


def user_count() -> int:
    from app.database.connection import session_scope

    with session_scope() as session:
        return int(session.scalar(select(func.count(User.id))) or 0)


def auth_required() -> bool:
    return google_login_enabled() or user_count() > 0


def normalize_role(value: str | None, *, admin: bool = False) -> str:
    if admin or str(value or "").strip().lower() == ROLE_ADMIN:
        return ROLE_ADMIN
    return ROLE_USER


def user_role(user: dict[str, Any] | None) -> str:
    if not user:
        return ROLE_USER
    return normalize_role(user.get("role"), admin=bool(user.get("is_admin")))


def user_is_admin(request: Request) -> bool:
    user = current_user(request)
    if user:
        return user_role(user) == ROLE_ADMIN
    return not auth_required()


def is_public_path(path: str) -> bool:
    if path.startswith("/static"):
        return True
    return path in PUBLIC_PATHS


def is_admin_path(path: str) -> bool:
    return path == "/sources" or path.startswith(ADMIN_PREFIXES)


def local_google_id(email: str) -> str:
    digest = hashlib.sha256((email or "").strip().lower().encode("utf-8")).hexdigest()[:40]
    return f"local:{digest}"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        _PBKDF2_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or stored.count("$") != 3:
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


def apply_role(row: User, role: str) -> User:
    row.role = normalize_role(role)
    row.is_admin = row.role == ROLE_ADMIN
    return row


def user_to_session(row: User) -> dict[str, Any]:
    role = normalize_role(getattr(row, "role", None), admin=bool(row.is_admin))
    return {
        "id": row.id,
        "email": row.email,
        "name": row.name or row.email,
        "picture": row.picture or "",
        "role": role,
        "is_admin": role == ROLE_ADMIN,
        "has_password": bool(row.password_hash),
    }


def count_admins(session: Session) -> int:
    return int(
        session.scalar(select(func.count(User.id)).where((User.role == ROLE_ADMIN) | (User.is_admin.is_(True)))) or 0
    )


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
            role=ROLE_ADMIN if admin else ROLE_USER,
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
            apply_role(row, ROLE_ADMIN)
        elif not getattr(row, "role", None):
            apply_role(row, ROLE_ADMIN if row.is_admin else ROLE_USER)
    row.last_login_at = utc_now()
    session.flush()
    return row


def create_local_user(
    session: Session,
    *,
    email: str,
    password: str,
    name: str = "",
    role: str = ROLE_USER,
) -> User:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("Enter a valid email address.")
    if len(password or "") < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")
    existing = session.scalar(select(User).where(func.lower(User.email) == email))
    if existing is not None:
        raise ValueError("An account with that email already exists.")
    assigned = normalize_role(role)
    if session.scalar(select(func.count(User.id))) == 0:
        assigned = ROLE_ADMIN
    row = User(
        google_id=local_google_id(email),
        email=email,
        name=(name or "").strip() or email.split("@")[0],
        password_hash=hash_password(password),
        is_admin=assigned == ROLE_ADMIN,
        role=assigned,
        last_login_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row


def seed_admin_account(
    email: str | None = None,
    password: str | None = None,
    name: str | None = None,
    *,
    reset_password: bool = False,
) -> dict[str, Any]:
    """Create the local admin if missing. Idempotent unless reset_password is set."""
    from app.database.connection import session_scope

    cfg = get_runtime_config()
    email = (email or cfg.env.admin_email or DEFAULT_ADMIN_EMAIL).strip().lower()
    password = password or cfg.env.admin_password or DEFAULT_ADMIN_PASSWORD
    name = (name or cfg.env.admin_name or DEFAULT_ADMIN_NAME).strip() or DEFAULT_ADMIN_NAME
    if not email or "@" not in email:
        raise ValueError("Enter a valid email address.")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")

    with session_scope() as session:
        row = session.scalar(select(User).where(func.lower(User.email) == email))
        if row is None:
            row = create_local_user(session, email=email, password=password, name=name, role=ROLE_ADMIN)
            apply_role(row, ROLE_ADMIN)
            return {"status": "created", "email": row.email, "name": row.name}

        changed = False
        if row.role != ROLE_ADMIN or not row.is_admin:
            apply_role(row, ROLE_ADMIN)
            changed = True
        if name and row.name != name:
            row.name = name
            changed = True
        if reset_password or not row.password_hash:
            row.password_hash = hash_password(password)
            changed = True
        if not changed:
            return {"status": "exists", "email": row.email, "name": row.name}
        session.flush()
        return {"status": "updated", "email": row.email, "name": row.name}


def authenticate_local(session: Session, email: str, password: str) -> User | None:
    email = (email or "").strip().lower()
    row = session.scalar(select(User).where(func.lower(User.email) == email))
    if row is None or not verify_password(password, row.password_hash):
        return None
    row.last_login_at = utc_now()
    session.flush()
    return row


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.created_at, User.id)).all())


def set_user_role(session: Session, user_id: int, role: str) -> User:
    row = session.get(User, user_id)
    if row is None:
        raise ValueError("User not found.")
    assigned = normalize_role(role)
    if assigned != ROLE_ADMIN and count_admins(session) <= 1 and (row.role == ROLE_ADMIN or row.is_admin):
        raise ValueError("Keep at least one admin account.")
    apply_role(row, assigned)
    session.flush()
    return row


def safe_next_path(value: str | None) -> str:
    text = (value or "").strip()
    if text.startswith("/") and not text.startswith("//"):
        return text
    return "/"
