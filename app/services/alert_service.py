"""Saved-search alerts: detect new metadata matches without downloading PDFs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import app_env
from app.database.connection import session_scope
from app.database.models import SavedSearch
from app.database.repository import find_existing_paper
from app.models.paper import PaperRecord
from app.models.search import SearchFilters
from app.utils.logger import get_logger
from app.utils.time import utc_now

logger = get_logger("app.alerts")

ALERT_FREQUENCIES = ("daily", "weekly", "monthly")
_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
_ALERT_PROVIDERS = ("openalex", "crossref", "arxiv", "pubmed")
_worker_task = None
_POLL_SECONDS = 300.0

ProbeFn = Callable[[SavedSearch], Awaitable[list[PaperRecord]]]


def alert_is_due(row: SavedSearch, *, now=None) -> bool:
    if not row.alert_enabled:
        return False
    current = now or utc_now()
    last = row.last_run_at
    if last is None:
        return True
    if last.tzinfo is None and getattr(current, "tzinfo", None) is not None:
        from datetime import UTC

        last = last.replace(tzinfo=UTC)
    days = _DAYS.get((row.alert_frequency or "weekly").lower(), 7)
    return current >= last + timedelta(days=days)


def record_alert_key(record: PaperRecord) -> str:
    if record.doi:
        return f"doi:{record.doi.strip().lower()}"
    if record.pmid:
        return f"pmid:{str(record.pmid).strip()}"
    if record.arxiv_id:
        return f"arxiv:{str(record.arxiv_id).strip().lower()}"
    return f"title:{(record.title or '').strip().lower()[:180]}"


def diff_alert_records(seen_keys: set[str], records: list[PaperRecord]) -> tuple[list[PaperRecord], set[str]]:
    new_records: list[PaperRecord] = []
    all_keys = set(seen_keys)
    for record in records:
        key = record_alert_key(record)
        if key in all_keys:
            continue
        all_keys.add(key)
        new_records.append(record)
    return new_records, all_keys


def apply_alert_results(session: Session, saved: SavedSearch, records: list[PaperRecord]) -> dict[str, Any]:
    extra = _filters(saved)
    alert_state = extra.get("_alert") if isinstance(extra.get("_alert"), dict) else {}
    seen = {str(item) for item in (alert_state.get("seen") or []) if item}
    new_records, all_keys = diff_alert_records(seen, records)
    in_library = 0
    preview = []
    for record in new_records:
        existing = find_existing_paper(session, record)
        if existing is not None:
            in_library += 1
        preview.append(
            {
                "title": record.title,
                "year": record.publication_year,
                "doi": record.doi,
                "in_library": existing is not None,
            }
        )
    extra["_alert"] = {"seen": sorted(all_keys)[-400:], "new": preview[:20]}
    saved.filters_json = json.dumps(extra)
    saved.last_run_at = utc_now()
    saved.last_result_count = len(records)
    saved.new_paper_count = len(new_records)
    session.flush()
    return {
        "new_count": len(new_records),
        "result_count": len(records),
        "already_in_library": in_library,
        "preview": preview[:20],
        "downloaded": False,
    }


def alert_preview(saved: SavedSearch) -> list[dict[str, Any]]:
    extra = _filters(saved)
    payload = extra.get("_alert") if isinstance(extra.get("_alert"), dict) else {}
    rows = payload.get("new") or []
    return [row for row in rows if isinstance(row, dict)]


async def probe_saved_search(saved: SavedSearch, *, limit: int = 20) -> list[PaperRecord]:
    from app.providers import build_providers
    from app.utils.http import AsyncHttpClient

    extra = _filters(saved)
    filters = SearchFilters(
        query=saved.query,
        year_from=extra.get("year_from"),
        year_to=extra.get("year_to"),
        source=extra.get("source"),
        open_access_only=bool(extra.get("open_access_only")),
        max_results=min(int(extra.get("max_results") or limit), limit),
        download=False,
    )
    records: list[PaperRecord] = []
    seen: set[str] = set()
    async with AsyncHttpClient() as client:
        providers = [provider for provider in build_providers(client) if provider.name in _ALERT_PROVIDERS]
        if extra.get("source"):
            providers = [provider for provider in providers if provider.name == extra.get("source")] or providers
        for provider in providers[:4]:
            try:
                found = await provider.search(saved.query, filters)
            except Exception:
                logger.exception("Alert probe failed for provider %s", provider.name)
                continue
            for record in found:
                key = record_alert_key(record)
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)
                if len(records) >= limit:
                    return records
    return records


async def run_saved_search_alert(saved_id: int, *, probe: ProbeFn | None = None) -> dict[str, Any]:
    with session_scope() as session:
        saved = session.get(SavedSearch, saved_id)
        if saved is None:
            return {"ok": False, "error": "Saved search not found"}
        snapshot = saved
        session.expunge(snapshot)
    records = await (probe(snapshot) if probe else probe_saved_search(snapshot))
    with session_scope() as session:
        saved = session.get(SavedSearch, saved_id)
        if saved is None:
            return {"ok": False, "error": "Saved search not found"}
        result = apply_alert_results(session, saved, records)
        result["ok"] = True
        result["name"] = saved.name
        return result


async def run_due_alerts(*, probe: ProbeFn | None = None) -> dict[str, Any]:
    with session_scope() as session:
        rows = list(session.scalars(select(SavedSearch).where(SavedSearch.alert_enabled.is_(True))).all())
        due_ids = [row.id for row in rows if alert_is_due(row)]
    ran = 0
    errors = 0
    for saved_id in due_ids:
        try:
            await run_saved_search_alert(saved_id, probe=probe)
            ran += 1
        except Exception:
            errors += 1
            logger.exception("Saved-search alert %s failed", saved_id)
    return {"ran": ran, "due": len(due_ids), "errors": errors}


async def start_alert_worker() -> None:
    global _worker_task
    if app_env() == "testing":
        return
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_alert_loop(), name="saved-search-alert-worker")
    logger.info("Saved-search alert worker started (poll every %.0fs)", _POLL_SECONDS)


async def _alert_loop() -> None:
    while True:
        try:
            await run_due_alerts()
        except Exception:
            logger.exception("Saved-search alert tick failed")
        await asyncio.sleep(_POLL_SECONDS)


def _filters(saved: SavedSearch) -> dict[str, Any]:
    try:
        payload = json.loads(saved.filters_json or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
