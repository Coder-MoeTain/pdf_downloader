"""PM2 process control for the Settings updates section."""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

_PM2_TIMEOUT = 60
_DEFAULT_NAME = "researchpaper"


class Pm2Error(RuntimeError):
    pass


def _pm2_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PM2_HOME", os.path.expanduser("~/.pm2"))
    return env


def _run_pm2(*args: str, timeout: int = _PM2_TIMEOUT) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["pm2", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_pm2_env(),
        )
    except FileNotFoundError as exc:
        raise Pm2Error("pm2 is not installed on this server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise Pm2Error("pm2 timed out.") from exc


def _clip(text: str, limit: int = 8000) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def pm2_process_name() -> str:
    return (os.environ.get("PM2_APP_NAME") or _DEFAULT_NAME).strip() or _DEFAULT_NAME


def pm2_status(name: str | None = None) -> dict[str, Any]:
    process = name or pm2_process_name()
    missing = {
        "ok": False,
        "error": "pm2 is not installed on this server.",
        "name": process,
        "status": "",
        "pid": None,
        "uptime": "",
        "restarts": None,
        "memory": "",
        "cpu": "",
    }
    try:
        result = _run_pm2("jlist")
    except Pm2Error as exc:
        missing["error"] = str(exc)
        return missing

    if result.returncode != 0:
        detail = _clip(result.stderr or result.stdout) or "pm2 jlist failed."
        missing["error"] = detail
        return missing

    try:
        processes = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        missing["error"] = "Could not parse pm2 process list."
        return missing

    match = next((item for item in processes if item.get("name") == process), None)
    if match is None:
        missing["error"] = f'PM2 process "{process}" was not found. Run pm2 list on the server.'
        return missing

    monit = match.get("monit") or {}
    pm2_env = match.get("pm2_env") or {}
    return {
        "ok": True,
        "error": "",
        "name": process,
        "status": str(pm2_env.get("status") or "unknown"),
        "pid": monit.get("pid") or pm2_env.get("pm_pid"),
        "uptime": _format_uptime(int(pm2_env.get("pm_uptime") or 0)),
        "restarts": pm2_env.get("restart_time"),
        "memory": _format_bytes(monit.get("memory")),
        "cpu": f'{monit.get("cpu", 0)}%',
    }


def pm2_restart(name: str | None = None) -> dict[str, Any]:
    process = name or pm2_process_name()
    result = _run_pm2("restart", process)
    output = _clip("\n".join(part for part in (result.stdout, result.stderr) if (part or "").strip()))
    if result.returncode != 0:
        raise Pm2Error(_clip(result.stderr or result.stdout) or f"pm2 restart {process} failed.")
    status = pm2_status(process)
    return {
        "ok": True,
        "output": output or f"Restarted {process}.",
        "status": status,
    }


def pm2_logs(name: str | None = None, *, lines: int = 120) -> dict[str, Any]:
    process = name or pm2_process_name()
    line_count = max(20, min(int(lines), 500))
    result = _run_pm2("logs", process, "--lines", str(line_count), "--nostream")
    output = _clip("\n".join(part for part in (result.stdout, result.stderr) if (part or "").strip()))
    if result.returncode != 0:
        raise Pm2Error(_clip(result.stderr or result.stdout) or f"pm2 logs {process} failed.")
    return {
        "ok": True,
        "output": output or f"No log lines returned for {process}.",
        "lines": line_count,
        "name": process,
    }


def _format_uptime(start_ms: int) -> str:
    if start_ms <= 0:
        return ""
    seconds = max(0, int(time.time() * 1000 - start_ms) // 1000)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _format_bytes(value: object | None) -> str:
    try:
        size = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    units = ["B", "KB", "MB", "GB"]
    amount = float(size)
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    if unit == "B":
        return f"{int(amount)} {unit}"
    return f"{amount:.1f} {unit}"
