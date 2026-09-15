"""Persisted audit events for security-sensitive actions. Never log secrets."""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from sqlalchemy import select

from app.database.connection import session_scope
from app.security.secrets import redact_secrets
from app.utils.logger import get_logger

logger = get_logger("app.audit")


def client_ip(request: Request | None) -> str:
    if request is None:
        return ""
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    if request.client:
        return (request.client.host or "")[:64]
    return ""


def record_audit(
    action: str,
    *,
    request: Request | None = None,
    actor_user_id: int | None = None,
    target_type: str = "",
    target_id: str = "",
    result: str = "success",
    metadata: dict[str, Any] | None = None,
) -> None:
    safe_meta = redact_secrets(metadata or {})
    ip = client_ip(request)
    if actor_user_id is None and request is not None:
        user = request.session.get("user") if hasattr(request, "session") else None
        if isinstance(user, dict) and user.get("id"):
            try:
                actor_user_id = int(user["id"])
            except (TypeError, ValueError):
                actor_user_id = None
    try:
        from app.database.models import AuditLog

        with session_scope() as session:
            session.add(
                AuditLog(
                    actor_user_id=actor_user_id,
                    action=action,
                    target_type=(target_type or "")[:64],
                    target_id=str(target_id or "")[:128],
                    ip=ip,
                    result=(result or "success")[:32],
                    metadata_json=json.dumps(safe_meta, default=str)[:4000],
                )
            )
    except Exception:
        logger.info(
            "audit action=%s result=%s actor=%s target=%s:%s ip=%s",
            action,
            result,
            actor_user_id,
            target_type,
            target_id,
            ip,
        )


def list_audit_logs(limit: int = 100) -> list[dict[str, Any]]:
    from app.database.models import AuditLog, User

    with session_scope() as session:
        rows = list(session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all())
        users = {
            row.id: (row.name or row.email)
            for row in session.scalars(
                select(User).where(User.id.in_({item.actor_user_id for item in rows if item.actor_user_id}))
            ).all()
        }
        payload = []
        for row in rows:
            meta: dict[str, Any] = {}
            if row.metadata_json:
                try:
                    parsed = json.loads(row.metadata_json)
                    if isinstance(parsed, dict):
                        meta = parsed
                except json.JSONDecodeError:
                    meta = {}
            payload.append(
                {
                    "id": row.id,
                    "timestamp": row.created_at,
                    "actor_user_id": row.actor_user_id,
                    "actor": users.get(row.actor_user_id) if row.actor_user_id else "anonymous",
                    "action": row.action,
                    "target_type": row.target_type,
                    "target_id": row.target_id,
                    "ip": row.ip,
                    "result": row.result,
                    "metadata": meta,
                }
            )
        return payload
