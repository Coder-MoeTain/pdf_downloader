"""Daily 06:00 refresh for GitHub top projects and Call for Papers."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta

from app.database.settings_repository import load_daily_refresh_last_run, mark_daily_refresh_run
from app.utils.logger import get_logger
from app.utils.time import as_utc, now_local, to_local, utc_now

logger = get_logger("app.daily_refresh")

DAILY_HOUR = 6
_POLL_SECONDS = 30.0
_worker_task: asyncio.Task | None = None
_run_lock = threading.Lock()
_running = False


def _parse_last_run(raw: str | None) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return as_utc(stamp)


def daily_slot(now: datetime) -> datetime:
    """Return today's 06:00 in the same timezone as `now`."""
    current = now
    if current.tzinfo is None:
        from datetime import UTC

        current = current.replace(tzinfo=UTC)
    return current.replace(hour=DAILY_HOUR, minute=0, second=0, microsecond=0)


def daily_refresh_is_due(*, now: datetime | None = None, last_run: datetime | None = None) -> bool:
    """True when local time is at/after 06:00 and today's slot has not run yet."""
    current = now or now_local()
    if current.tzinfo is None:
        from datetime import UTC

        current = current.replace(tzinfo=UTC)
    today_run = daily_slot(current)
    if current < today_run:
        return False
    if last_run is None:
        return True
    last_local = to_local(last_run) or last_run
    if last_local.tzinfo is None:
        last_local = last_local.replace(tzinfo=current.tzinfo)
    else:
        last_local = last_local.astimezone(current.tzinfo)
    return last_local < today_run


def next_daily_refresh_at(*, now: datetime | None = None, last_run: datetime | None = None) -> datetime:
    current = now or now_local()
    today_run = daily_slot(current)
    if daily_refresh_is_due(now=current, last_run=last_run):
        return today_run
    if current < today_run:
        return today_run
    return today_run + timedelta(days=1)


def run_daily_refresh_once(*, force: bool = False) -> dict[str, object]:
    """Refresh GitHub top repos and WikiCFP when the 06:00 slot is due."""
    global _running
    with _run_lock:
        if _running:
            return {"due": False, "skipped": "busy"}
        last = _parse_last_run(load_daily_refresh_last_run())
        if not force and not daily_refresh_is_due(last_run=last):
            return {"due": False, "github": {}, "cfp": {}}
        _running = True
    github_stats: dict[str, object] = {}
    cfp_stats: dict[str, object] = {}
    try:
        from app.services.cfp_service import refresh_cfps
        from app.services.github_service import refresh_github_repos

        logger.info("Daily 06:00 refresh started")
        github_stats = refresh_github_repos()
        cfp_stats = refresh_cfps(force=True)
        mark_daily_refresh_run(utc_now())
        logger.info("Daily 06:00 refresh finished github=%s cfp=%s", github_stats, cfp_stats)
        return {"due": True, "github": github_stats, "cfp": cfp_stats}
    except Exception:
        logger.exception("Daily 06:00 refresh failed")
        mark_daily_refresh_run(utc_now())
        return {"due": True, "github": github_stats, "cfp": cfp_stats, "error": 1}
    finally:
        with _run_lock:
            _running = False


async def _schedule_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_daily_refresh_once)
        except Exception:
            logger.exception("Daily refresh tick failed")
        await asyncio.sleep(_POLL_SECONDS)


async def start_daily_refresh_worker() -> None:
    global _worker_task
    from app.config import app_env

    if app_env() == "testing":
        logger.info("Daily refresh worker skipped in testing")
        return
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_schedule_loop(), name="daily-refresh-worker")
    logger.info("Daily refresh worker started (06:00 local, poll every %.0fs)", _POLL_SECONDS)
