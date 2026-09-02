"""Lightweight schema bootstrap. SQLAlchemy create_all is the migration path for v1."""

from __future__ import annotations

from app.database.connection import init_db


def migrate() -> None:
    init_db()
