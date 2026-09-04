"""Online presence and usage events for the Settings activity page."""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import delete, func, select

from app.database.connection import retry_on_sqlite_lock, session_scope
from app.database.models import UsageEvent, User
from app.utils.time import format_local, utc_now

ONLINE_SECONDS = 5 * 60
FLUSH_SECONDS = 45
EVENT_KEEP = 500
_PRESENCE: dict[int, dict[str, Any]] = {}
_FLUSHED: dict[int, float] = {}
_LOCK = threading.Lock()

ACTION_LABELS = {
    "login": "Signed in",
    "logout": "Signed out",
    "search": "Search",
    "download": "Download",
    "settings": "Settings",
}


def client_ip(request: Request | None) -> str:
    if request is None:
        return ""
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:64]
    if request.client and request.client.host:
        return str(request.client.host)[:64]
    return ""


def _clip(text: str, limit: int = 400) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _user_label(user: dict[str, Any] | None) -> str:
    if not user:
        return "Anonymous"
    return str(user.get("name") or user.get("email") or "User")


def _ago(seconds: int) -> str:
    if seconds < 15:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def touch_presence(user: dict[str, Any] | None, *, path: str = "") -> None:
    if not user or user.get("id") is None:
        return
    try:
        user_id = int(user["id"])
    except (TypeError, ValueError):
        return
    now = utc_now()
    record = {
        "id": user_id,
        "name": _user_label(user),
        "email": str(user.get("email") or ""),
        "role": str(user.get("role") or ("admin" if user.get("is_admin") else "user")),
        "path": (path or "")[:255],
        "seen_at": now,
    }
    with _LOCK:
        _PRESENCE[user_id] = record
        last = _FLUSHED.get(user_id, 0)
        due = (now.timestamp() - last) >= FLUSH_SECONDS
        if due:
            _FLUSHED[user_id] = now.timestamp()
    if due:
        def _flush() -> None:
            with session_scope() as session:
                row = session.get(User, user_id)
                if row is not None:
                    row.last_seen_at = now

        try:
            retry_on_sqlite_lock(_flush)
        except Exception:
            pass


def drop_presence(user_id: int | None) -> None:
    if user_id is None:
        return
    with _LOCK:
        _PRESENCE.pop(int(user_id), None)
        _FLUSHED.pop(int(user_id), None)


def list_online() -> list[dict[str, Any]]:
    cutoff = utc_now() - timedelta(seconds=ONLINE_SECONDS)
    by_id: dict[int, dict[str, Any]] = {}
    with _LOCK:
        for user_id, row in _PRESENCE.items():
            seen = row.get("seen_at")
            if seen is not None and seen >= cutoff:
                by_id[user_id] = dict(row)
    try:
        with session_scope() as session:
            rows = session.scalars(select(User).where(User.last_seen_at.is_not(None), User.last_seen_at >= cutoff)).all()
            for row in rows:
                if row.id in by_id:
                    continue
                by_id[row.id] = {
                    "id": row.id,
                    "name": row.name or row.email,
                    "email": row.email,
                    "role": row.role or ("admin" if row.is_admin else "user"),
                    "path": "",
                    "seen_at": row.last_seen_at,
                }
    except Exception:
        pass
    items = []
    now = utc_now()
    for row in by_id.values():
        seen = row.get("seen_at")
        seconds = max(0, int((now - seen).total_seconds())) if seen is not None else 0
        items.append(
            {
                **row,
                "seen_at": seen,
                "seen_label": format_local(seen, "%H:%M:%S") if seen is not None else "",
                "ago": _ago(seconds),
            }
        )
    items.sort(key=lambda item: item.get("seen_at") or utc_now(), reverse=True)
    return items


def log_event(
    *,
    action: str,
    detail: str = "",
    user_id: int | None = None,
    user_label: str = "",
    path: str = "",
    ip: str = "",
) -> None:
    def _write() -> None:
        with session_scope() as session:
            session.add(
                UsageEvent(
                    user_id=user_id,
                    user_label=_clip(user_label, 255),
                    action=(action or "other")[:32],
                    detail=_clip(detail),
                    path=(path or "")[:255],
                    ip=(ip or "")[:64],
                    created_at=utc_now(),
                )
            )
            session.flush()
            total = int(session.scalar(select(func.count(UsageEvent.id))) or 0)
            extra = total - EVENT_KEEP
            if extra > 0:
                oldest = session.scalars(select(UsageEvent.id).order_by(UsageEvent.id).limit(extra)).all()
                if oldest:
                    session.execute(delete(UsageEvent).where(UsageEvent.id.in_(list(oldest))))

    try:
        retry_on_sqlite_lock(_write)
    except Exception:
        pass


def record_usage(request: Request | None, action: str, detail: str = "") -> None:
    user = None
    path = ""
    ip = ""
    if request is not None:
        from app.auth import current_user

        user = current_user(request)
        path = request.url.path
        ip = client_ip(request)
    user_id = None
    if user and user.get("id") is not None:
        try:
            user_id = int(user["id"])
        except (TypeError, ValueError):
            user_id = None
    log_event(
        action=action,
        detail=detail,
        user_id=user_id,
        user_label=_user_label(user),
        path=path,
        ip=ip,
    )


def list_events(limit: int = 80) -> list[dict[str, Any]]:
    cap = max(10, min(int(limit), 200))
    with session_scope() as session:
        rows = session.scalars(select(UsageEvent).order_by(UsageEvent.id.desc()).limit(cap)).all()
        items = []
        for row in rows:
            person = row.user
            label = row.user_label or ((person.name or person.email) if person else "Anonymous")
            items.append(
                {
                    "id": row.id,
                    "user_id": row.user_id,
                    "user": label,
                    "email": person.email if person else "",
                    "action": row.action,
                    "action_label": ACTION_LABELS.get(row.action, row.action.replace("_", " ").title()),
                    "detail": row.detail or "",
                    "path": row.path or "",
                    "ip": row.ip or "",
                    "created_at": row.created_at,
                    "time": format_local(row.created_at, "%Y-%m-%d %H:%M:%S") if row.created_at else "",
                }
            )
        return items


def activity_payload() -> dict[str, Any]:
    online = list_online()
    return {
        "online": [
            {
                "id": row["id"],
                "name": row["name"],
                "email": row["email"],
                "role": row["role"],
                "path": row["path"],
                "ago": row["ago"],
                "seen_label": row["seen_label"],
            }
            for row in online
        ],
        "online_count": len(online),
        "events": [
            {
                "id": row["id"],
                "user": row["user"],
                "action": row["action"],
                "action_label": row["action_label"],
                "detail": row["detail"],
                "ip": row["ip"],
                "time": row["time"],
            }
            for row in list_events()
        ],
        "window_minutes": ONLINE_SECONDS // 60,
    }


def reset_presence() -> None:
    with _LOCK:
        _PRESENCE.clear()
        _FLUSHED.clear()
