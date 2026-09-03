"""Fast-forward git pull for the Settings update button."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from app.config import ROOT_DIR

_GIT_TIMEOUT = 90
_SECRET_REMOTE = re.compile(r"(https?://)([^/@]+)@", re.I)


class GitUpdateError(RuntimeError):
    pass


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.pop("GIT_ASKPASS", None)
    return env


def run_git(*args: str, timeout: int = _GIT_TIMEOUT) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_git_env(),
        )
    except FileNotFoundError as exc:
        raise GitUpdateError("git is not installed on this server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitUpdateError("git timed out.") from exc


def _clip(text: str, limit: int = 4000) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _safe_remote(url: str) -> str:
    return _SECRET_REMOTE.sub(r"\1", (url or "").strip())


def git_status() -> dict[str, Any]:
    missing = {
        "ok": False,
        "error": "This folder is not a git repository.",
        "branch": "",
        "commit": "",
        "short": "",
        "subject": "",
        "remote": "",
        "dirty": False,
    }
    try:
        inside = run_git("rev-parse", "--is-inside-work-tree")
    except GitUpdateError as exc:
        missing["error"] = str(exc)
        return missing
    if inside.returncode != 0 or (inside.stdout or "").strip() != "true":
        return missing

    branch = (run_git("rev-parse", "--abbrev-ref", "HEAD").stdout or "").strip() or "HEAD"
    short = (run_git("rev-parse", "--short", "HEAD").stdout or "").strip()
    commit = (run_git("rev-parse", "HEAD").stdout or "").strip()
    subject = (run_git("log", "-1", "--pretty=%s").stdout or "").strip()
    remote = _safe_remote((run_git("remote", "get-url", "origin").stdout or "").strip())
    porcelain = run_git("status", "--porcelain")
    dirty = bool((porcelain.stdout or "").strip())
    return {
        "ok": True,
        "error": "",
        "branch": branch,
        "commit": commit,
        "short": short,
        "subject": subject,
        "remote": remote,
        "dirty": dirty,
    }


def git_pull() -> dict[str, Any]:
    status = git_status()
    if not status["ok"]:
        raise GitUpdateError(status["error"])
    if status["dirty"]:
        raise GitUpdateError("Local files have uncommitted changes. Commit or stash them on the server before pulling.")

    fetch = run_git("fetch", "--all", "--prune")
    if fetch.returncode != 0:
        detail = _clip(fetch.stderr or fetch.stdout) or "git fetch failed."
        raise GitUpdateError(detail)

    pull = run_git("pull", "--ff-only")
    output = _clip("\n".join(part for part in (fetch.stdout, pull.stdout, pull.stderr) if (part or "").strip()))
    if pull.returncode != 0:
        raise GitUpdateError(_clip(pull.stderr or pull.stdout) or "git pull --ff-only failed.")

    after = git_status()
    already = "already up to date" in (pull.stdout or "").lower()
    return {
        "ok": True,
        "already_current": already,
        "output": output or ("Already up to date." if already else "Pulled latest changes."),
        "status": after,
    }
