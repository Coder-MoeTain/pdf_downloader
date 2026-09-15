"""In-process counters for health and optional Prometheus text."""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict

_lock = threading.Lock()
_started = time.time()
_counters: dict[str, float] = defaultdict(float)


def inc(name: str, value: float = 1.0) -> None:
    with _lock:
        _counters[name] += value


def set_gauge(name: str, value: float) -> None:
    with _lock:
        _counters[name] = value


def snapshot() -> dict[str, float]:
    with _lock:
        data = dict(_counters)
    try:
        import psutil

        proc = psutil.Process(os.getpid())
        data["memory_rss"] = float(proc.memory_info().rss)
    except Exception:
        data.setdefault("memory_rss", 0.0)
    data["uptime_seconds"] = time.time() - _started
    return data


def prometheus_text() -> str:
    lines = ["# TYPE cyber_scholar_metric gauge"]
    for key, value in sorted(snapshot().items()):
        metric = "cyber_scholar_" + key.replace(".", "_").replace("-", "_")
        lines.append(f"{metric} {value}")
    return "\n".join(lines) + "\n"
