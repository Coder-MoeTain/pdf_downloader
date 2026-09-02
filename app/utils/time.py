"""UTC timestamps stored as naive datetimes for SQLite."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
