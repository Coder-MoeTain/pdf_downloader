"""PM2 process control for the Settings updates section."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

_PM2_TIMEOUT = 60
_DEFAULT_NAME = "researchpaper"


class Pm2Error(RuntimeError):
    pass


def _pm2_env(*, silent: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PM2_HOME", os.path.expanduser("~/.pm2"))
    if silent:
        env["PM2_SILENT"] = "true"
    return env


def _pm2_bin() -> str:
    for name in ("pm2", "pm2.cmd"):
        found = shutil.which(name)
        if found:
            return found
    raise Pm2Error("pm2 is not installed on this server.")


def _run_pm2(*args: str, timeout: int = _PM2_TIMEOUT, silent: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [_pm2_bin(), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=_pm2_env(silent=silent),
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


def _pm2_home() -> Path:
    return Path(os.environ.get("PM2_HOME") or Path.home() / ".pm2")


def _extract_json(text: str) -> Any:
    blob = (text or "").strip()
    if not blob:
        raise json.JSONDecodeError("empty", blob, 0)
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        pass
    for line in reversed(blob.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    decoder = json.JSONDecoder()
    for index, char in enumerate(blob):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(blob[index:])
            return value
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("no json", blob, 0)


def _as_process_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("processes", "data", "list", "apps"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _parse_jlist(stdout: str, stderr: str = "") -> list[dict[str, Any]]:
    blob = "\n".join(part for part in (stdout, stderr) if (part or "").strip())
    return _as_process_list(_extract_json(blob))


def _load_dump() -> list[dict[str, Any]]:
    path = _pm2_home() / "dump.pm2"
    if not path.is_file():
        return []
    try:
        return _as_process_list(_extract_json(path.read_text(encoding="utf-8", errors="replace")))
    except (OSError, json.JSONDecodeError):
        return []


def _process_names(item: dict[str, Any]) -> set[str]:
    env = item.get("pm2_env") if isinstance(item.get("pm2_env"), dict) else {}
    names = [
        item.get("name"),
        env.get("name"),
        item.get("namespace"),
    ]
    namespace = str(env.get("namespace") or item.get("namespace") or "").strip()
    name = str(item.get("name") or env.get("name") or "").strip()
    if namespace and name and namespace != "default":
        names.append(f"{namespace}:{name}")
    return {str(value).strip() for value in names if value}


def _find_process(processes: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    wanted = (name or "").strip()
    if not wanted:
        return None
    for item in processes:
        if wanted in _process_names(item):
            return item
    lowered = wanted.lower()
    for item in processes:
        if lowered in {value.lower() for value in _process_names(item)}:
            return item
    return None


def _empty_status(process: str, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "available": False,
        "error": error,
        "name": process,
        "status": "",
        "pid": None,
        "uptime": "",
        "restarts": None,
        "memory": "",
        "cpu": "",
    }


def pm2_process_name() -> str:
    return (os.environ.get("PM2_APP_NAME") or _DEFAULT_NAME).strip() or _DEFAULT_NAME


def pm2_status(name: str | None = None) -> dict[str, Any]:
    process = name or pm2_process_name()
    missing = _empty_status(process, "pm2 is not installed on this server.")
    try:
        result = _run_pm2("jlist")
    except Pm2Error as exc:
        missing["error"] = str(exc)
        return missing

    missing["available"] = True
    raw = "\n".join(part for part in (result.stdout, result.stderr) if (part or "").strip())
    parsed = False
    try:
        processes = _parse_jlist(result.stdout or "", result.stderr or "")
        parsed = True
    except json.JSONDecodeError:
        processes = []

    match = _find_process(processes, process)
    if match is None:
        dump = _load_dump()
        match = _find_process(dump, process)
        if match is not None:
            processes = dump
    if match is None:
        if not parsed and not processes:
            snippet = _clip(raw, 400) or "(empty)"
            missing["error"] = f"Could not parse pm2 process list.\n{snippet}"
            return missing
        known = sorted({label for item in processes for label in _process_names(item)})
        found = ", ".join(known[:8]) if known else "none"
        missing["error"] = (
            f'PM2 process "{process}" was not found. '
            f"Known processes: {found}. Set PM2_APP_NAME in .env if the name differs."
        )
        return missing

    monit = match.get("monit") if isinstance(match.get("monit"), dict) else {}
    pm2_env = match.get("pm2_env") if isinstance(match.get("pm2_env"), dict) else {}
    return {
        "ok": True,
        "available": True,
        "error": "",
        "name": process,
        "status": str(pm2_env.get("status") or match.get("status") or "unknown"),
        "pid": monit.get("pid") or pm2_env.get("pm_pid") or match.get("pid"),
        "uptime": _format_uptime(int(pm2_env.get("pm_uptime") or 0)),
        "restarts": pm2_env.get("restart_time") if pm2_env.get("restart_time") is not None else match.get("restart_time"),
        "memory": _format_bytes(monit.get("memory") or match.get("memory")),
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
