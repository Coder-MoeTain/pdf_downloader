"""Background open-access download batches without blocking the web UI."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Literal

from app.services.download_service import download_open_access_papers, resume_downloading_papers
from app.services.progress import download_tracker
from app.utils.logger import get_logger

logger = get_logger("app.download_queue")

_worker_task: asyncio.Task | None = None
_queue: asyncio.Queue[DownloadJob | None] = asyncio.Queue()
_running = False


@dataclasses.dataclass(frozen=True)
class DownloadJob:
    kind: Literal["oa", "resume"] = "oa"
    search_id: int | None = None
    user_id: int | None = None
    limit: int = 100


def oa_download_active() -> bool:
    snap = download_tracker.snapshot()
    return bool(snap.get("active")) or _running


def enqueue_oa_download(*, search_id: int | None, user_id: int | None) -> bool:
    """Queue an OA batch download. Returns False when one is already running."""
    global _running
    if _running or oa_download_active():
        return False
    _queue.put_nowait(DownloadJob(kind="oa", search_id=search_id, user_id=user_id))
    return True


def enqueue_resume_downloads(*, user_id: int | None, limit: int = 100) -> bool:
    """Queue a resume of stuck DOWNLOADING papers. Returns False when busy."""
    if _running or oa_download_active():
        return False
    _queue.put_nowait(DownloadJob(kind="resume", user_id=user_id, limit=limit))
    return True


def _run_batch_sync(job: DownloadJob) -> dict[str, int]:
    if job.kind == "resume":
        return asyncio.run(resume_downloading_papers(user_id=job.user_id, limit=job.limit))
    return asyncio.run(download_open_access_papers(search_id=job.search_id, user_id=job.user_id))


async def _worker_loop() -> None:
    global _running
    while True:
        job = await _queue.get()
        if job is None:
            _queue.task_done()
            break
        _running = True
        try:
            stats = await asyncio.to_thread(_run_batch_sync, job)
            label = "Resume" if job.kind == "resume" else "OA"
            logger.info(
                "%s download batch finished: %s saved, %s failed, %s skipped",
                label,
                stats.get("downloaded", 0),
                stats.get("failed", 0),
                stats.get("skipped", 0),
            )
        except Exception:
            logger.exception("Download batch failed")
        finally:
            _running = False
            _queue.task_done()


async def start_download_worker() -> None:
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker_loop(), name="download-queue-worker")


async def stop_download_worker() -> None:
    global _worker_task
    if _worker_task is None:
        return
    await _queue.put(None)
    await _worker_task
    _worker_task = None
