"""Background worker that copies downloaded PDFs into e-library without blocking search."""

from __future__ import annotations

import threading
import time
from typing import Iterable

from app.services.lms_sync import load_lms_sync_config, maybe_sync_to_lms
from app.utils.logger import get_logger

logger = get_logger("app.lms_watch")

_lock = threading.Lock()
_wake = threading.Event()
_stop = threading.Event()
_thread: threading.Thread | None = None
_pending_ids: set[int] = set()
_sweep_all = False
_interval = 20.0


def schedule_lms_sync(*, paper_ids: Iterable[int] | None = None) -> None:
    """Queue an e-library import and return immediately."""
    ids = [int(x) for x in (paper_ids or []) if x]
    start_lms_watch()
    with _lock:
        global _sweep_all
        if ids:
            _pending_ids.update(ids)
        else:
            _sweep_all = True
    _wake.set()


def start_lms_watch(*, interval: float = 20.0) -> None:
    global _thread, _interval
    _interval = max(5.0, float(interval))
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_run_loop, name="lms-sync", daemon=True)
        _thread.start()
    logger.info("e-library import watcher started (every %.0fs, and after each download)", _interval)


def stop_lms_watch() -> None:
    _stop.set()
    _wake.set()


def run_lms_watch_forever(*, interval: float = 15.0) -> None:
    """CLI / PM2 entry: keep importing in the background until killed."""
    cfg = load_lms_sync_config()
    if not cfg.enabled:
        logger.warning("LMS_SYNC_ENABLED=false — watcher idle")
    elif cfg.root is None:
        logger.warning("LMS_ROOT not found — set LMS_ROOT in .env (example: /var/www/elibrary)")
    else:
        logger.info("Importing into %s", cfg.root)
    start_lms_watch(interval=interval)
    schedule_lms_sync()
    try:
        while not _stop.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        stop_lms_watch()


def _take_work() -> list[int] | None:
    """None means sweep every unsynced download; a list means those paper ids."""
    global _sweep_all
    with _lock:
        if _sweep_all:
            _sweep_all = False
            _pending_ids.clear()
            return None
        if _pending_ids:
            ids = list(_pending_ids)
            _pending_ids.clear()
            return ids
    return []


def _run_loop() -> None:
    while not _stop.is_set():
        woken = _wake.wait(timeout=_interval)
        _wake.clear()
        if _stop.is_set():
            break
        work = _take_work()
        if work == [] and not woken:
            work = None
        if work == []:
            continue
        maybe_sync_to_lms(paper_ids=work)
