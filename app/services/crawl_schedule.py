"""Scheduled source crawler driven by Settings."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.database.connection import session_scope
from app.database.repository import active_crawl_job_for_source
from app.database.settings_repository import load_crawl_schedule, mark_crawl_schedule_run
from app.services.crawl_queue import enqueue_crawl
from app.services.crawl_service import filters_from_form
from app.utils.logger import get_logger

logger = get_logger("app.crawl_schedule")

_worker_task: asyncio.Task | None = None
_POLL_SECONDS = 30.0


def _parse_last_run(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def schedule_is_due(settings: dict, *, now: datetime | None = None) -> bool:
    if not settings.get("enabled"):
        return False
    if not settings.get("sources"):
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    last = _parse_last_run(str(settings.get("last_run") or ""))
    if last is None:
        return True
    interval = max(15, int(settings.get("interval_minutes") or 60))
    return current >= last + timedelta(minutes=interval)


def next_run_at(settings: dict) -> datetime | None:
    if not settings.get("enabled"):
        return None
    last = _parse_last_run(str(settings.get("last_run") or ""))
    interval = max(15, int(settings.get("interval_minutes") or 60))
    if last is None:
        return datetime.now(timezone.utc)
    return last + timedelta(minutes=interval)


def _crawlable_slugs() -> set[str]:
    from app.database.settings_repository import list_academic_sources, source_is_available, source_to_dict
    from app.database.settings_store import settings_session
    from app.providers import PROVIDER_CLASSES

    browse = {cls.name for cls in PROVIDER_CLASSES if getattr(cls, "supports_browse", False)}
    slugs: set[str] = set()
    with settings_session() as session:
        for row in list_academic_sources(session):
            item = source_to_dict(row)
            if item["slug"] in browse and source_is_available(row):
                slugs.add(item["slug"])
    return slugs


def run_scheduled_crawl_once(*, force: bool = False) -> dict[str, object]:
    """Enqueue scheduled crawls when due. Returns a summary dict."""
    settings = load_crawl_schedule()
    if not force and not schedule_is_due(settings):
        return {"queued": [], "skipped": [], "due": False}

    selected = list(settings.get("sources") or [])
    if not selected:
        return {"queued": [], "skipped": [], "due": True, "reason": "no_sources"}

    crawlable = _crawlable_slugs()
    queued: list[str] = []
    skipped: list[str] = []

    for slug in selected:
        if slug not in crawlable:
            skipped.append(slug)
            continue
        with session_scope() as session:
            if active_crawl_job_for_source(session, slug) is not None:
                skipped.append(slug)
                continue
        filters = filters_from_form(
            source=slug,
            query=str(settings.get("query") or ""),
            open_access_only=bool(settings.get("open_access_only")),
            skip_existing=bool(settings.get("skip_existing", True)),
            download=bool(settings.get("download")),
            pdfs_only=bool(settings.get("pdfs_only")),
            page_size=100,
            max_pages=int(settings.get("max_pages") or 10),
            max_papers=int(settings.get("max_papers") or 500),
        )
        enqueue_crawl(user_id=None, filters=filters, scheduled=True)
        queued.append(slug)

    mark_crawl_schedule_run()
    if queued:
        logger.info("Scheduled crawl queued %s source(s): %s", len(queued), ", ".join(queued))
    elif skipped:
        logger.info("Scheduled crawl due but nothing queued (skipped: %s)", ", ".join(skipped))
    return {"queued": queued, "skipped": skipped, "due": True}


async def _schedule_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_scheduled_crawl_once)
        except Exception:
            logger.exception("Scheduled crawl tick failed")
        await asyncio.sleep(_POLL_SECONDS)


async def start_crawl_schedule_worker() -> None:
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_schedule_loop(), name="crawl-schedule-worker")
    logger.info("Crawl schedule worker started (poll every %.0fs)", _POLL_SECONDS)
