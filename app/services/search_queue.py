"""Persistent search queue with per-user parallel execution."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any

from app.database.connection import session_scope
from app.database.repository import (
    claim_next_search_job,
    complete_search_job,
    get_search_job,
    queue_position,
    search_jobs_grouped_by_user,
)
from app.models.search import SearchFilters, SortMode
from app.services.progress import job_registry
from app.services.search_service import SearchCancelled, SearchService
from app.utils.logger import get_logger

logger = get_logger("app.search_queue")

_worker_task: asyncio.Task | None = None
_running_job_ids: set[int] = set()
_running_tasks: dict[int, asyncio.Task] = {}
_cancel_requested: set[int] = set()
_lock = asyncio.Lock()
_loop: asyncio.AbstractEventLoop | None = None


def filters_from_dict(data: dict[str, Any]) -> SearchFilters:
    sort_raw = data.get("sort", "relevance")
    try:
        sort_mode = SortMode(sort_raw) if isinstance(sort_raw, str) else SortMode.RELEVANCE
    except ValueError:
        sort_mode = SortMode.RELEVANCE
    return SearchFilters(
        query=str(data.get("query") or ""),
        year_from=data.get("year_from"),
        year_to=data.get("year_to"),
        authors=data.get("authors"),
        journal=data.get("journal"),
        publisher=data.get("publisher"),
        source=data.get("source"),
        open_access_only=bool(data.get("open_access_only")),
        max_results=int(data.get("max_results") or 50),
        min_citations=int(data.get("min_citations") or 0),
        sort=sort_mode,
        download=bool(data.get("download", True)),
        download_limit=data.get("download_limit"),
        max_file_size=data.get("max_file_size"),
        topic_name=data.get("topic_name"),
    )


def job_to_dict(job, *, position: int | None = None) -> dict[str, Any]:
    username = job.user.email if job.user else "Anonymous"
    name = job.user.name if job.user and job.user.name else username
    return {
        "id": job.id,
        "user_id": job.user_id,
        "username": username,
        "name": name,
        "query": job.query,
        "status": job.status,
        "position": position,
        "error": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "search_query_id": job.search_query_id,
        "can_stop": False,
    }


def queue_snapshot(*, user_id: int | None = None, is_admin: bool = False) -> dict[str, Any]:
    with session_scope() as session:
        grouped = search_jobs_grouped_by_user(session)
        users: list[dict[str, Any]] = []
        for username in sorted(grouped.keys()):
            jobs = grouped[username]
            if not is_admin and user_id is not None:
                if not any(j.user_id == user_id for j in jobs):
                    continue
            entries = []
            for job in jobs:
                pos = queue_position(session, job.id) if job.status == "pending" else None
                if not is_admin and user_id is not None and job.user_id != user_id:
                    continue
                item = job_to_dict(job, position=pos)
                owner = user_id is not None and job.user_id == user_id
                item["can_stop"] = job.status in ("pending", "running") and (is_admin or owner)
                entries.append(item)
            if entries:
                users.append({"username": username, "jobs": entries})
        running = sum(1 for jobs in grouped.values() for j in jobs if j.status == "running")
        pending = sum(1 for jobs in grouped.values() for j in jobs if j.status == "pending")
    return {"users": users, "running": running, "pending": pending}


async def _run_job(job_id: int) -> None:
    async with _lock:
        if job_id in _running_job_ids:
            return
        _running_job_ids.add(job_id)

    try:
        if job_id in _cancel_requested:
            with session_scope() as session:
                complete_search_job(session, job_id, status="cancelled", error_message="Stopped by user.")
            return
        with session_scope() as session:
            job = get_search_job(session, job_id)
            if job is None or job.status != "running":
                return
            query = job.query
            filters_data = json.loads(job.filters_json)
            user_id = job.user_id

        filters = filters_from_dict(filters_data)
        progress = job_registry.create(job_id)
        if job_id in _cancel_requested:
            progress.request_cancel()
            progress.finish_search(cancelled=True)
            with session_scope() as session:
                complete_search_job(session, job_id, status="cancelled", error_message="Stopped by user.")
            return
        progress.start_search(query)
        if job_id in _cancel_requested or progress.is_cancelled():
            raise SearchCancelled("Search stopped.")
        progress.log(f"Search job #{job_id} started for {query}", "info")

        service = SearchService(progress=progress)
        stats = await service.run(filters, user_id=user_id, skip_progress_start=True)

        with session_scope() as session:
            row = get_search_job(session, job_id)
            if row is None or row.status == "cancelled":
                return
            complete_search_job(
                session,
                job_id,
                status="completed",
                search_query_id=stats.search_query_id,
            )

        logger.info("Search job %s completed: %s unique papers", job_id, stats.unique_papers)
    except SearchCancelled:
        logger.info("Search job %s stopped", job_id)
        prog = job_registry.get(job_id)
        if prog:
            prog.finish_search(cancelled=True)
        with session_scope() as session:
            complete_search_job(session, job_id, status="cancelled", error_message="Stopped by user.")
    except asyncio.CancelledError:
        logger.info("Search job %s cancelled", job_id)
        prog = job_registry.get(job_id)
        if prog:
            prog.request_cancel()
            prog.finish_search(cancelled=True)
        with session_scope() as session:
            complete_search_job(session, job_id, status="cancelled", error_message="Stopped by user.")
    except Exception as exc:
        logger.exception("Search job %s failed", job_id)
        prog = job_registry.get(job_id)
        if prog:
            prog.finish_search(error=str(exc))
        with session_scope() as session:
            complete_search_job(session, job_id, status="failed", error_message=str(exc))
    finally:
        _cancel_requested.discard(job_id)
        _running_tasks.pop(job_id, None)
        async with _lock:
            _running_job_ids.discard(job_id)


async def _dispatch_pending() -> None:
    while True:
        job_id: int | None = None
        with session_scope() as session:
            job = claim_next_search_job(session)
            if job is not None:
                job_id = job.id
        if job_id is not None:
            task = asyncio.create_task(_run_job(job_id))
            _running_tasks[job_id] = task
        await asyncio.sleep(0.4)


async def start_search_queue_worker() -> None:
    global _worker_task, _loop
    _loop = asyncio.get_running_loop()
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_dispatch_pending(), name="search-queue-worker")
    logger.info("Search queue worker started")


def enqueue_search(*, user_id: int | None, query: str, filters: dict) -> int:
    from app.database.repository import enqueue_search_job

    payload = dataclasses.asdict(filters) if hasattr(filters, "__dataclass_fields__") else dict(filters)
    if hasattr(filters, "sort") and hasattr(filters.sort, "value"):
        payload["sort"] = filters.sort.value
    with session_scope() as session:
        job = enqueue_search_job(session, user_id=user_id, query=query, filters=payload)
        return job.id


def _interrupt_running_job(job_id: int) -> None:
    task = _running_tasks.get(job_id)
    if task is not None and not task.done():
        task.cancel()


def cancel_search(job_id: int, *, user_id: int | None, is_admin: bool = False) -> str:
    """Stop a pending or running search. Returns pending|running."""
    with session_scope() as session:
        job = get_search_job(session, job_id)
        if job is None:
            raise ValueError("Search job not found.")
        owner = user_id is not None and job.user_id == user_id
        if not is_admin and not owner:
            raise PermissionError("You can only stop your own search.")
        if job.status not in ("pending", "running"):
            raise ValueError("That search is not in the queue.")
        was = job.status
        complete_search_job(session, job_id, status="cancelled", error_message="Stopped by user.")
        if was == "pending":
            return was

    _cancel_requested.add(job_id)
    prog = job_registry.get(job_id)
    if prog:
        prog.request_cancel()
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None
    if running_loop is not None:
        _interrupt_running_job(job_id)
    elif _loop is not None and _loop.is_running():
        _loop.call_soon_threadsafe(_interrupt_running_job, job_id)
    else:
        _interrupt_running_job(job_id)
    return was
