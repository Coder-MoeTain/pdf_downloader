"""Persistent crawl queue with per-user parallel execution."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any

from app.database.connection import session_scope
from app.database.repository import (
    claim_next_crawl_job,
    complete_crawl_job,
    crawl_job_is_scheduled,
    crawl_jobs_grouped_by_user,
    crawl_queue_position,
    get_crawl_job,
)
from app.models.crawl import CrawlFilters
from app.services.crawl_service import CrawlCancelled, CrawlService
from app.services.progress import crawl_job_registry
from app.utils.logger import get_logger
from app.utils.time import format_local, utc_now

logger = get_logger("app.crawl_queue")

_worker_task: asyncio.Task | None = None
_running_job_ids: set[int] = set()
_running_tasks: dict[int, asyncio.Task] = {}
_cancel_requested: set[int] = set()
_lock = asyncio.Lock()
_loop: asyncio.AbstractEventLoop | None = None


def filters_from_dict(data: dict[str, Any]) -> CrawlFilters:
    return CrawlFilters(
        source=str(data.get("source") or ""),
        query=str(data.get("query") or ""),
        year_from=data.get("year_from"),
        year_to=data.get("year_to"),
        open_access_only=bool(data.get("open_access_only")),
        skip_existing=bool(data.get("skip_existing", True)),
        download=bool(data.get("download", False)),
        pdfs_only=bool(data.get("pdfs_only")),
        page_size=int(data.get("page_size") or 100),
        max_pages=int(data.get("max_pages") or 0),
        max_papers=int(data.get("max_papers") or 50000),
        download_limit=data.get("download_limit"),
        max_file_size=data.get("max_file_size"),
        topic_name=data.get("topic_name"),
    )


def job_to_dict(job, *, position: int | None = None, source_labels: dict[str, str] | None = None) -> dict[str, Any]:
    scheduled = crawl_job_is_scheduled(job)
    username = "Schedule" if scheduled else (job.user.email if job.user else "Anonymous")
    name = "Schedule" if scheduled else (job.user.name if job.user and job.user.name else username)
    labels = source_labels or {}
    source_slug = job.source or ""
    return {
        "id": job.id,
        "user_id": job.user_id,
        "username": username,
        "name": name,
        "scheduled": scheduled,
        "source": source_slug,
        "source_label": labels.get(source_slug, source_slug.replace("_", " ").title()),
        "status": job.status,
        "position": position,
        "error": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "can_stop": False,
    }


def queue_snapshot(*, user_id: int | None = None, is_admin: bool = False) -> dict[str, Any]:
    from app.providers import source_display_names

    source_labels = source_display_names()
    with session_scope() as session:
        grouped = crawl_jobs_grouped_by_user(session)
        users: list[dict[str, Any]] = []
        for username in sorted(grouped.keys()):
            jobs = grouped[username]
            if not is_admin and user_id is not None:
                if not any(j.user_id == user_id for j in jobs):
                    continue
            entries = []
            for job in jobs:
                pos = crawl_queue_position(session, job.id) if job.status == "pending" else None
                if not is_admin and user_id is not None and job.user_id != user_id:
                    continue
                item = job_to_dict(job, position=pos, source_labels=source_labels)
                owner = user_id is not None and job.user_id == user_id
                item["can_stop"] = job.status in ("pending", "running") and (is_admin or owner)
                entries.append(item)
            if entries:
                users.append({"username": username, "jobs": entries})
        running = sum(1 for jobs in grouped.values() for j in jobs if j.status == "running")
        pending = sum(1 for jobs in grouped.values() for j in jobs if j.status == "pending")
    return {"users": users, "running": running, "pending": pending}


def db_crawl_progress_snapshot(job, *, position: int | None = None) -> dict[str, Any]:
    """Synthetic progress when in-memory crawl logs are not available yet."""
    stamp = format_local(utc_now(), "%H:%M:%S")
    source = job.source or ""
    base: dict[str, Any] = {
        "job_id": job.id,
        "kind": "crawl",
        "query": source,
        "current": 0,
        "total": 0,
        "percent": None,
        "stats": {},
        "logs": [],
    }
    if job.status == "pending":
        waiting = f"Queued (#{position} in line)" if position else "Queued · waiting to start…"
        base.update(
            active=True,
            phase="starting",
            percent=2,
            message=waiting,
            logs=[{"time": stamp, "level": "info", "message": f"Crawl queued: {source}"}],
        )
    elif job.status == "running":
        base.update(
            active=True,
            phase="starting",
            percent=5,
            message="Crawl is running…",
            logs=[{"time": stamp, "level": "info", "message": f"Crawl job #{job.id} is running…"}],
        )
    elif job.status == "completed":
        base.update(
            active=False,
            phase="done",
            percent=100,
            message="Crawl completed.",
            logs=[{"time": stamp, "level": "success", "message": f"Crawl completed: {source}"}],
        )
    elif job.status == "cancelled":
        base.update(
            active=False,
            phase="cancelled",
            message="Crawl stopped.",
            logs=[{"time": stamp, "level": "warning", "message": "Crawl stopped by user."}],
        )
    elif job.status == "failed":
        err = job.error_message or "Crawl failed."
        base.update(
            active=False,
            phase="error",
            error=err,
            message=err,
            logs=[{"time": stamp, "level": "danger", "message": err}],
        )
    return base


def crawl_progress_snapshot(job_id: int) -> dict[str, Any] | None:
    snap = crawl_job_registry.snapshot(job_id)
    if snap:
        return snap
    with session_scope() as session:
        job = get_crawl_job(session, job_id)
        if job is None:
            return None
        pos = crawl_queue_position(session, job_id) if job.status == "pending" else None
        return db_crawl_progress_snapshot(job, position=pos)


def _run_crawl_sync(filters: CrawlFilters, user_id: int | None, progress) -> Any:
    """Execute a crawl on a worker thread with its own event loop."""
    try:
        return asyncio.run(
            CrawlService(progress=progress).run(filters, user_id=user_id, skip_progress_start=True)
        )
    except CrawlCancelled:
        # Ensure the cancel flag stays set for any late observers.
        progress.request_cancel("Stopping crawl…")
        raise


async def _run_job(job_id: int) -> None:
    async with _lock:
        if job_id in _running_job_ids:
            return
        _running_job_ids.add(job_id)

    try:
        if job_id in _cancel_requested:
            with session_scope() as session:
                complete_crawl_job(session, job_id, status="cancelled", error_message="Stopped by user.")
            return
        with session_scope() as session:
            job = get_crawl_job(session, job_id)
            if job is None or job.status != "running":
                return
            filters_data = json.loads(job.filters_json)
            user_id = job.user_id
            source = job.source

        filters = filters_from_dict(filters_data)
        progress = crawl_job_registry.get_or_create(job_id)
        if job_id in _cancel_requested:
            progress.request_cancel("Stopping crawl…")
            progress.finish_crawl(cancelled=True)
            with session_scope() as session:
                complete_crawl_job(session, job_id, status="cancelled", error_message="Stopped by user.")
            return
        progress.mark_crawl_started(source)
        if filters_data.get("scheduled"):
            progress.log(f"Scheduled crawl job #{job_id} started for {source}", "info")
        else:
            progress.log(f"Crawl job #{job_id} started for {source}", "info")

        # Run off the uvicorn event loop so page loads stay responsive.
        # Do not cancel this awaiter — cooperative stop via progress.request_cancel().
        stats = await asyncio.to_thread(_run_crawl_sync, filters, user_id, progress)

        with session_scope() as session:
            row = get_crawl_job(session, job_id)
            if row is None or row.status == "cancelled":
                prog = crawl_job_registry.get(job_id)
                if prog and prog.snapshot().get("phase") != "cancelled":
                    prog.finish_crawl(cancelled=True)
                return
            complete_crawl_job(
                session,
                job_id,
                status="completed",
                papers_found=stats.new_papers,
                pdfs_downloaded=stats.pdfs_downloaded,
                pdfs_failed=stats.failed_downloads,
                records_seen=stats.records_seen,
                skipped_existing=stats.skipped_existing,
            )
        logger.info("Crawl job %s completed for %s", job_id, source)
    except CrawlCancelled:
        prog = crawl_job_registry.get(job_id)
        if prog:
            prog.finish_crawl(cancelled=True)
        with session_scope() as session:
            complete_crawl_job(session, job_id, status="cancelled", error_message="Stopped by user.")
        logger.info("Crawl job %s stopped", job_id)
    except asyncio.CancelledError:
        # Only cooperative cancel stops the worker thread; still mark cancelled if the task dies.
        prog = crawl_job_registry.get(job_id)
        if prog:
            prog.request_cancel("Stopping crawl…")
            prog.finish_crawl(cancelled=True)
        with session_scope() as session:
            complete_crawl_job(session, job_id, status="cancelled", error_message="Stopped by user.")
        raise
    except Exception as exc:
        logger.exception("Crawl job %s failed", job_id)
        prog = crawl_job_registry.get(job_id)
        if prog:
            prog.finish_crawl(error=str(exc))
        with session_scope() as session:
            complete_crawl_job(session, job_id, status="failed", error_message=str(exc))
    finally:
        _cancel_requested.discard(job_id)
        _running_tasks.pop(job_id, None)
        async with _lock:
            _running_job_ids.discard(job_id)


async def _dispatch_pending() -> None:
    from app.config import get_runtime_config

    def _claim(max_per_user: int) -> int | None:
        with session_scope() as session:
            job = claim_next_crawl_job(session, max_per_user=max_per_user)
            return job.id if job is not None else None

    while True:
        cfg = get_runtime_config()
        max_per_user = max(1, int(getattr(cfg, "max_concurrent_search_jobs_per_user", 1) or 1))
        for _ in range(32):
            job_id = await asyncio.to_thread(_claim, max_per_user)
            if job_id is None:
                break
            task = asyncio.create_task(_run_job(job_id))
            _running_tasks[job_id] = task
        await asyncio.sleep(0.4)


async def start_crawl_queue_worker() -> None:
    global _worker_task, _loop
    _loop = asyncio.get_running_loop()
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_dispatch_pending(), name="crawl-queue-worker")
    logger.info("Crawl queue worker started")


def enqueue_crawl(*, user_id: int | None, filters: CrawlFilters, scheduled: bool = False) -> int:
    from app.database.repository import enqueue_crawl_job

    payload = dataclasses.asdict(filters)
    if scheduled:
        payload["scheduled"] = True
    with session_scope() as session:
        job = enqueue_crawl_job(session, user_id=user_id, source=filters.source, filters=payload)
        job_id = job.id
    prog = crawl_job_registry.register_queued_crawl(job_id, filters.source)
    if scheduled:
        prog.log(f"Queued by schedule for {filters.source}", "info")
    return job_id


def _signal_running_job_stop(job_id: int) -> None:
    """Ask a running crawl to stop cooperatively (do not cancel the worker thread awaiter)."""
    prog = crawl_job_registry.get_or_create(job_id)
    prog.request_cancel("Stopping crawl…")


def cancel_crawl(job_id: int, *, user_id: int | None, is_admin: bool = False) -> str:
    with session_scope() as session:
        job = get_crawl_job(session, job_id)
        if job is None:
            raise ValueError("Crawl job not found.")
        owner = user_id is not None and job.user_id == user_id
        if not is_admin and not owner:
            raise PermissionError("You can only stop your own crawl.")
        if job.status not in ("pending", "running"):
            raise ValueError("That crawl is not in the queue.")
        was = job.status
        complete_crawl_job(session, job_id, status="cancelled", error_message="Stopped by user.")
        if was == "pending":
            return was

    _cancel_requested.add(job_id)
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None
    if running_loop is not None:
        _signal_running_job_stop(job_id)
    elif _loop is not None and _loop.is_running():
        _loop.call_soon_threadsafe(_signal_running_job_stop, job_id)
    else:
        _signal_running_job_stop(job_id)
    return was
