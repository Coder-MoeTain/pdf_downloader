"""In-memory download progress for the dashboard."""

from __future__ import annotations

import threading
from typing import Any


class ProgressTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = self._empty()

    def _empty(self) -> dict[str, Any]:
        return {
            "active": False,
            "current": 0,
            "total": 0,
            "downloaded": 0,
            "failed": 0,
            "skipped": 0,
            "paper_id": None,
            "title": "",
            "bytes_downloaded": 0,
            "bytes_total": None,
            "percent": None,
            "message": "",
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start_batch(self, total: int, message: str = "") -> None:
        with self._lock:
            self._state = self._empty()
            self._state["active"] = True
            self._state["total"] = max(total, 0)
            self._state["message"] = message or f"Starting {total} download{'s' if total != 1 else ''}"

    def begin_item(self, paper_id: int, title: str, index: int) -> None:
        with self._lock:
            self._state["active"] = True
            self._state["paper_id"] = paper_id
            self._state["title"] = title
            self._state["current"] = index
            self._state["bytes_downloaded"] = 0
            self._state["bytes_total"] = None
            self._state["percent"] = None
            total = self._state["total"] or index
            self._state["message"] = f"Downloading {index} of {total}"

    def update_bytes(self, received: int, total: int | None) -> None:
        with self._lock:
            self._state["bytes_downloaded"] = received
            self._state["bytes_total"] = total
            if total and total > 0:
                self._state["percent"] = round(min(100.0, received * 100.0 / total), 1)
            else:
                self._state["percent"] = None

    def finish_item(self, status: str) -> None:
        with self._lock:
            if status == "DOWNLOADED":
                self._state["downloaded"] += 1
                self._state["percent"] = 100.0
            elif status == "FAILED":
                self._state["failed"] += 1
            else:
                self._state["skipped"] += 1

    def finish_batch(self) -> None:
        with self._lock:
            self._state["active"] = False
            if self._state["total"]:
                done = self._state["downloaded"] + self._state["failed"] + self._state["skipped"]
                self._state["current"] = min(done, self._state["total"])
            self._state["message"] = (
                f"Finished: {self._state['downloaded']} downloaded, "
                f"{self._state['failed']} failed, {self._state['skipped']} skipped"
            )


tracker = ProgressTracker()
