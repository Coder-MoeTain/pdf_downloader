"""Collect host CPU, memory, disk, network, and process metrics for the admin health page."""

from __future__ import annotations

import os
import platform
import socket
import threading
import time
from collections import deque
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover - optional until installed
    psutil = None  # type: ignore[assignment]

HISTORY_LEN = 60
_lock = threading.Lock()
_history: dict[str, deque[float]] = {
    "cpu": deque(maxlen=HISTORY_LEN),
    "memory": deque(maxlen=HISTORY_LEN),
    "swap": deque(maxlen=HISTORY_LEN),
    "net_sent_mbps": deque(maxlen=HISTORY_LEN),
    "net_recv_mbps": deque(maxlen=HISTORY_LEN),
}
_last_net: tuple[float, int, int] | None = None
_boot_cpu_primed = False


def _bytes_human(n: float) -> str:
    value = float(max(n, 0))
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024 or unit == "PB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def _pct_tone(pct: float) -> str:
    if pct >= 90:
        return "danger"
    if pct >= 75:
        return "warning"
    if pct >= 50:
        return "info"
    return "success"


def _uptime_label(seconds: float) -> str:
    total = max(int(seconds), 0)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    if not days and not hours:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _network_rates() -> tuple[float, float, int, int]:
    """Return (sent_mbps, recv_mbps, bytes_sent, bytes_recv)."""
    global _last_net
    counters = psutil.net_io_counters()
    now = time.monotonic()
    sent = int(counters.bytes_sent)
    recv = int(counters.bytes_recv)
    sent_mbps = 0.0
    recv_mbps = 0.0
    with _lock:
        if _last_net is not None:
            prev_t, prev_sent, prev_recv = _last_net
            dt = max(now - prev_t, 1e-6)
            sent_mbps = max((sent - prev_sent) * 8 / dt / 1_000_000, 0.0)
            recv_mbps = max((recv - prev_recv) * 8 / dt / 1_000_000, 0.0)
        _last_net = (now, sent, recv)
    return sent_mbps, recv_mbps, sent, recv


def _disk_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for part in psutil.disk_partitions(all=False):
        mount = part.mountpoint
        if mount in seen:
            continue
        # Skip pseudo / optical mounts that often raise on Windows.
        opts = (part.opts or "").lower()
        fstype = (part.fstype or "").lower()
        if "cdrom" in opts or fstype in {"cdfs", "iso9660", "udf"}:
            continue
        try:
            usage = psutil.disk_usage(mount)
        except (PermissionError, OSError):
            continue
        seen.add(mount)
        pct = float(usage.percent)
        rows.append(
            {
                "device": part.device,
                "mount": mount,
                "fstype": part.fstype or "",
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "total_label": _bytes_human(usage.total),
                "used_label": _bytes_human(usage.used),
                "free_label": _bytes_human(usage.free),
                "percent": round(pct, 1),
                "tone": _pct_tone(pct),
            }
        )
    rows.sort(key=lambda item: item["percent"], reverse=True)
    return rows


def _process_rows(limit: int = 12) -> list[dict[str, Any]]:
    procs: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_percent", "memory_info", "status"]):
        try:
            info = proc.info
            mem = info.get("memory_info")
            rss = int(getattr(mem, "rss", 0) or 0)
            cpu = float(info.get("cpu_percent") or 0.0)
            mem_pct = float(info.get("memory_percent") or 0.0)
            procs.append(
                {
                    "pid": int(info.get("pid") or 0),
                    "name": str(info.get("name") or "unknown")[:64],
                    "user": str(info.get("username") or "—")[:40],
                    "cpu": round(cpu, 1),
                    "memory": round(mem_pct, 1),
                    "rss": rss,
                    "rss_label": _bytes_human(rss),
                    "status": str(info.get("status") or ""),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    procs.sort(key=lambda row: (row["cpu"], row["memory"], row["rss"]), reverse=True)
    return procs[: max(1, min(limit, 40))]


def _push_history(cpu: float, memory: float, swap: float, sent_mbps: float, recv_mbps: float) -> dict[str, list[float]]:
    with _lock:
        _history["cpu"].append(round(cpu, 2))
        _history["memory"].append(round(memory, 2))
        _history["swap"].append(round(swap, 2))
        _history["net_sent_mbps"].append(round(sent_mbps, 3))
        _history["net_recv_mbps"].append(round(recv_mbps, 3))
        return {key: list(values) for key, values in _history.items()}


def collect_system_health(*, process_limit: int = 12) -> dict[str, Any]:
    """Snapshot host metrics. Safe to call from request handlers."""
    if psutil is None:
        return {
            "ok": False,
            "error": "psutil is not installed. Run: pip install psutil",
            "collected_at": time.time(),
        }

    global _boot_cpu_primed
    if not _boot_cpu_primed:
        # First cpu_percent call always returns 0; prime once.
        psutil.cpu_percent(interval=None)
        for proc in psutil.process_iter(["cpu_percent"]):
            try:
                _ = proc.info
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        _boot_cpu_primed = True
        time.sleep(0.15)

    cpu_total = float(psutil.cpu_percent(interval=None))
    per_cpu = [float(v) for v in psutil.cpu_percent(interval=None, percpu=True)]
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    sent_mbps, recv_mbps, bytes_sent, bytes_recv = _network_rates()
    history = _push_history(cpu_total, float(vm.percent), float(swap.percent), sent_mbps, recv_mbps)

    boot = float(psutil.boot_time())
    load_avg: list[float] | None = None
    try:
        load_avg = [round(float(v), 2) for v in os.getloadavg()]
    except (AttributeError, OSError):
        load_avg = None

    freq = None
    try:
        f = psutil.cpu_freq()
        if f is not None:
            freq = {
                "current_mhz": round(float(f.current or 0), 1),
                "min_mhz": round(float(f.min or 0), 1) if f.min else None,
                "max_mhz": round(float(f.max or 0), 1) if f.max else None,
            }
    except (AttributeError, NotImplementedError, OSError):
        freq = None

    disks = _disk_rows()
    primary_disk = disks[0] if disks else None

    return {
        "ok": True,
        "collected_at": time.time(),
        "host": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "boot_time": boot,
            "uptime_seconds": max(time.time() - boot, 0),
            "uptime_label": _uptime_label(time.time() - boot),
            "pid": os.getpid(),
        },
        "cpu": {
            "percent": round(cpu_total, 1),
            "tone": _pct_tone(cpu_total),
            "count_logical": psutil.cpu_count(logical=True) or 0,
            "count_physical": psutil.cpu_count(logical=False) or 0,
            "per_cpu": [round(v, 1) for v in per_cpu],
            "load_avg": load_avg,
            "frequency": freq,
        },
        "memory": {
            "percent": round(float(vm.percent), 1),
            "tone": _pct_tone(float(vm.percent)),
            "total": int(vm.total),
            "used": int(vm.used),
            "available": int(vm.available),
            "total_label": _bytes_human(vm.total),
            "used_label": _bytes_human(vm.used),
            "available_label": _bytes_human(vm.available),
        },
        "swap": {
            "percent": round(float(swap.percent), 1),
            "tone": _pct_tone(float(swap.percent)),
            "total": int(swap.total),
            "used": int(swap.used),
            "free": int(swap.free),
            "total_label": _bytes_human(swap.total),
            "used_label": _bytes_human(swap.used),
            "free_label": _bytes_human(swap.free),
        },
        "storage": {
            "disks": disks,
            "primary": primary_disk,
        },
        "network": {
            "sent_mbps": round(sent_mbps, 3),
            "recv_mbps": round(recv_mbps, 3),
            "bytes_sent": bytes_sent,
            "bytes_recv": bytes_recv,
            "bytes_sent_label": _bytes_human(bytes_sent),
            "bytes_recv_label": _bytes_human(bytes_recv),
        },
        "processes": {
            "total": len(psutil.pids()),
            "top": _process_rows(process_limit),
        },
        "history": history,
    }
