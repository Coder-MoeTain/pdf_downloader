"""In-memory job progress and search logs for the dashboard."""

from __future__ import annotations

import threading
from app.utils.time import format_local, utc_now
from typing import Any

MAX_LOGS = 120
SEARCH_PHASES = ("starting", "searching", "merging", "oa", "storing", "downloading", "done", "error")


class ProgressTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = self._empty()

    def _empty(self) -> dict[str, Any]:
        return {
            "active": False,
            "kind": "",
            "phase": "idle",
            "query": "",
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
            "error": "",
            "logs": [],
            "stats": {},
            "providers_done": 0,
            "providers_total": 0,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            payload = dict(self._state)
            payload["logs"] = list(self._state["logs"])
            payload["stats"] = dict(self._state["stats"])
            return payload

    def _stamp(self) -> str:
        return format_local(utc_now(), "%H:%M:%S")

    def _append_log(self, message: str, level: str = "info") -> None:
        entry = {"time": self._stamp(), "level": level, "message": message}
        logs = self._state["logs"]
        logs.append(entry)
        if len(logs) > MAX_LOGS:
            del logs[: len(logs) - MAX_LOGS]

    def log(self, message: str, level: str = "info") -> None:
        with self._lock:
            self._append_log(message, level)
            self._state["message"] = message

    def start_search(self, query: str) -> None:
        with self._lock:
            self._state = self._empty()
            self._state["active"] = True
            self._state["kind"] = "search"
            self._state["phase"] = "starting"
            self._state["query"] = query
            self._state["percent"] = 2
            self._state["message"] = f"Starting search for “{query}”"
            self._append_log(f"Queued search: {query}", "info")

    def set_providers_total(self, total: int) -> None:
        with self._lock:
            self._state["providers_total"] = max(total, 0)
            self._state["providers_done"] = 0

    def set_phase(self, phase: str, message: str, *, current: int | None = None, total: int | None = None, percent: float | None = None) -> None:
        with self._lock:
            self._state["phase"] = phase
            self._state["message"] = message
            if current is not None:
                self._state["current"] = current
            if total is not None:
                self._state["total"] = total
            if percent is not None:
                self._state["percent"] = percent
            self._append_log(message, "info")

    def provider_finished(self, name: str, count: int | None = None, error: str | None = None) -> None:
        with self._lock:
            self._state["providers_done"] = int(self._state["providers_done"]) + 1
            done = int(self._state["providers_done"])
            total = int(self._state["providers_total"]) or done
            self._state["current"] = done
            self._state["total"] = total
            self._state["percent"] = round(8 + (done / total) * 37, 1)
            if error:
                self._append_log(f"{name}: {error}", "danger")
            else:
                self._append_log(f"{name}: {count or 0} results", "success")

    def update_stats(self, **values: Any) -> None:
        with self._lock:
            self._state["stats"].update(values)

    def finish_search(self, *, error: str | None = None, stats: dict[str, Any] | None = None) -> None:
        with self._lock:
            if stats:
                self._state["stats"].update(stats)
            self._state["active"] = False
            if error:
                self._state["phase"] = "error"
                self._state["error"] = error
                self._state["message"] = error
                self._append_log(error, "danger")
            else:
                self._state["phase"] = "done"
                self._state["percent"] = 100
                unique = self._state["stats"].get("unique_papers", 0)
                self._state["message"] = f"Search complete · {unique} unique papers"
                self._append_log(self._state["message"], "success")

    def start_batch(self, total: int, message: str = "") -> None:
        with self._lock:
            nested = self._state.get("kind") == "search" and self._state.get("active")
            if not nested:
                logs = list(self._state.get("logs") or [])
                query = self._state.get("query") or ""
                self._state = self._empty()
                self._state["kind"] = "download"
                self._state["logs"] = logs
                self._state["query"] = query
            self._state["active"] = True
            self._state["current"] = 0
            self._state["downloaded"] = 0
            self._state["failed"] = 0
            self._state["skipped"] = 0
            self._state["total"] = max(total, 0)
            self._state["paper_id"] = None
            self._state["title"] = ""
            self._state["bytes_downloaded"] = 0
            self._state["bytes_total"] = None
            self._state["percent"] = None
            self._state["message"] = message or f"Starting {total} download{'s' if total != 1 else ''}"
            if nested:
                self._state["phase"] = "downloading"
                self._state["percent"] = 82
            self._append_log(self._state["message"], "info")

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
            if self._state.get("kind") == "search":
                self._state["phase"] = "downloading"
                self._state["percent"] = round(82 + (index / max(total, 1)) * 13, 1)

    def update_bytes(self, received: int, total: int | None) -> None:
        with self._lock:
            self._state["bytes_downloaded"] = received
            self._state["bytes_total"] = total
            if total and total > 0:
                file_pct = min(100.0, received * 100.0 / total)
                if self._state.get("kind") == "search":
                    batch = int(self._state["total"]) or 1
                    current = max(int(self._state["current"]) - 1, 0)
                    self._state["percent"] = round(82 + ((current + file_pct / 100) / batch) * 13, 1)
                else:
                    self._state["percent"] = round(file_pct, 1)
            elif self._state.get("kind") != "search":
                self._state["percent"] = None

    def finish_item(self, status: str) -> None:
        with self._lock:
            if status == "DOWNLOADED":
                self._state["downloaded"] += 1
                if self._state.get("kind") != "search":
                    self._state["percent"] = 100.0
            elif status == "FAILED":
                self._state["failed"] += 1
            else:
                self._state["skipped"] += 1

    def finish_batch(self) -> None:
        with self._lock:
            nested = self._state.get("kind") == "search"
            if self._state["total"]:
                done = self._state["downloaded"] + self._state["failed"] + self._state["skipped"]
                self._state["current"] = min(done, self._state["total"])
            message = (
                f"Finished: {self._state['downloaded']} downloaded, "
                f"{self._state['failed']} failed, {self._state['skipped']} skipped"
            )
            self._state["message"] = message
            self._append_log(message, "info")
            if nested:
                self._state["percent"] = 96
                return
            self._state["active"] = False


tracker = ProgressTracker()
