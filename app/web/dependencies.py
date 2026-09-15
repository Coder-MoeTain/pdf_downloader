"""Shared template context and helpers for web routers."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __app_subtitle__, __version__
from app.auth import (
    PASSWORD_MIN_LENGTH,
    ROLE_ADMIN,
    ROLE_USER,
    auth_required,
    current_user,
    google_login_enabled,
    list_users,
    safe_next_path,
    setup_required,
    user_is_admin,
    user_role,
)
from app.config import ROOT_DIR, get_runtime_config
from app.database.models import Paper
from app.database.repository import crawl_job_keyword, split_tags
from app.database.settings_repository import (
    list_academic_sources,
    load_crawl_schedule,
    seed_academic_sources,
    source_to_dict,
)
from app.database.settings_store import store_status
from app.database.source_catalog import SOURCE_KEY_FIELDS
from app.providers import PROVIDER_CLASSES
from app.security.audit import list_audit_logs
from app.services.cfp_service import cfp_display_image
from app.services.crawl_schedule import next_run_at
from app.services.download_service import has_claimed_local_pdf, pdf_button_state, safe_library_pdf
from app.services.progress import download_tracker, live_progress
from app.services.usage import activity_payload
from app.utils.git_update import git_status
from app.utils.pm2_control import pm2_status
from app.utils.time import format_local, now_local, timezone_abbrev, timezone_choices, timezone_offset_label
from app.web.flash import GIT_LOG_KEY, PM2_LOG_KEY, get_job_log, pop_flash, set_flash
from app.web.ui import (
    active_page,
    download_actor_name,
    download_record_date,
    downloads_href,
    is_new_download,
    job_actor_name,
    job_status_meta,
    library_href,
    paper_abstract_meta,
    paper_authors_line,
    paper_categories,
    paper_citations,
    paper_downloader_name,
    paper_record_date,
    reports_href,
    source_homepage,
    source_label,
    source_logo_url,
    sources_href,
    status_meta,
)

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
templates.env.globals["pdf_button_state"] = pdf_button_state
templates.env.globals["status_meta"] = status_meta
templates.env.globals["can_preview"] = has_claimed_local_pdf
templates.env.globals["has_claimed_local_pdf"] = has_claimed_local_pdf
templates.env.globals["library_href"] = library_href
templates.env.globals["downloads_href"] = downloads_href
templates.env.globals["reports_href"] = reports_href
templates.env.globals["sources_href"] = sources_href
templates.env.globals["source_label"] = source_label
templates.env.globals["source_logo_url"] = source_logo_url
templates.env.globals["source_homepage"] = source_homepage
templates.env.globals["paper_abstract_meta"] = paper_abstract_meta
templates.env.globals["paper_authors_line"] = paper_authors_line
templates.env.globals["paper_citations"] = paper_citations
templates.env.globals["paper_categories"] = paper_categories
templates.env.globals["paper_downloader_name"] = paper_downloader_name
templates.env.globals["paper_record_date"] = paper_record_date
templates.env.globals["download_actor_name"] = download_actor_name
templates.env.globals["download_record_date"] = download_record_date
templates.env.globals["is_new_download"] = is_new_download
templates.env.globals["job_actor_name"] = job_actor_name
templates.env.globals["job_status_meta"] = job_status_meta
templates.env.globals["crawl_job_keyword"] = crawl_job_keyword
templates.env.globals["cfp_display_image"] = cfp_display_image
templates.env.filters["localdt"] = lambda value, fmt="%Y-%m-%d %H:%M": format_local(value, fmt)
templates.env.filters["tags"] = split_tags


def csrf_token_global(request: Request) -> str:
    from app.security.csrf import get_csrf_token

    try:
        return get_csrf_token(request)
    except Exception:
        return ""


def _csrf_token_global(request: Request) -> str:
    return csrf_token_global(request)


templates.env.globals["csrf_token"] = _csrf_token_global
templates.env.globals["csrf_field"] = lambda request: (
    f'<input type="hidden" name="csrf_token" value="{_csrf_token_global(request)}">'
)
templates.env.filters["filesize"] = lambda value: _format_bytes(value)

_SOURCE_ROWS_TTL = 300.0


_source_rows_cache: list[dict] | None = None


_source_rows_cache_at = 0.0


def _login_ctx(request: Request, next_url: str = "/") -> dict:
    return _ctx(
        request,
        next_url=safe_next_path(next_url),
        google_ready=google_login_enabled(),
        allow_register=False,
        setup_mode=setup_required(),
        password_min=PASSWORD_MIN_LENGTH,
    )


def _account_users(session):
    return [
        {
            "id": row.id,
            "email": row.email,
            "name": row.name,
            "role": row.role or (ROLE_ADMIN if row.is_admin else ROLE_USER),
            "has_password": bool(row.password_hash),
            "last_login_at": row.last_login_at,
        }
        for row in list_users(session)
    ]


def _request_user_id(request: Request) -> int | None:
    user = current_user(request)
    if not user:
        return None
    try:
        return int(user["id"])
    except (KeyError, TypeError, ValueError):
        return None


def _ctx(request: Request, **extra):
    cfg = get_runtime_config()
    session_user = current_user(request)
    payload = {
        "request": request,
        "app_name": cfg.name,
        "app_subtitle": cfg.subtitle or __app_subtitle__,
        "version": __version__,
        "flash": pop_flash(request),
        "csrf_token": _csrf_token_global(request),
        "page": active_page(request.url.path),
        "progress": live_progress(),
        "download_progress": download_tracker.snapshot(),
        "timezone": cfg.timezone,
        "timezone_abbrev": timezone_abbrev(cfg.timezone),
        "timezone_offset": timezone_offset_label(cfg.timezone),
        "show_paywalled": cfg.show_paywalled,
        "user": session_user,
        "is_admin": user_is_admin(request),
        "auth_enabled": True,
        "auth_required": auth_required(),
        "google_ready": google_login_enabled(),
        "role": user_role(session_user) if session_user else "user",
    }
    payload.update(extra)
    # Never let page kwargs (e.g. filter user ids) overwrite the session identity.
    payload["user"] = session_user
    payload["is_admin"] = user_is_admin(request)
    payload["role"] = user_role(session_user) if session_user else "user"
    payload["page"] = active_page(request.url.path)
    payload["csrf_token"] = _csrf_token_global(request)
    return payload


def _source_rows() -> list[dict]:
    global _source_rows_cache, _source_rows_cache_at
    now = time.monotonic()
    if _source_rows_cache is not None and (now - _source_rows_cache_at) < _SOURCE_ROWS_TTL:
        return _source_rows_cache
    seed_academic_sources()
    searchable = {cls.name for cls in PROVIDER_CLASSES}
    sources = []
    for row in list_academic_sources():
        item = source_to_dict(row)
        item["searchable"] = row.slug in searchable
        sources.append(item)
    _source_rows_cache = sources
    _source_rows_cache_at = now
    return sources


def _crawl_source_rows() -> list[dict]:
    """All configured sources with crawl capability flags (same catalog as /sources)."""
    browse = {cls.name: bool(getattr(cls, "supports_browse", False)) for cls in PROVIDER_CLASSES}
    rows: list[dict] = []
    for item in _source_rows():
        row = dict(item)
        row["supports_browse"] = browse.get(item["slug"], False)
        row["crawlable"] = row["supports_browse"] and item["available"]
        rows.append(row)
    return rows


def _crawl_source_lookup() -> dict[str, dict]:
    return {row["slug"]: row for row in _crawl_source_rows()}


def _settings_ctx(request: Request, section: str = "workspace"):
    cfg = get_runtime_config()
    sources = _source_rows()
    credentials = []
    for slug, field in SOURCE_KEY_FIELDS.items():
        match = next((s for s in sources if s["slug"] == slug), None)
        credentials.append(
            {
                "slug": slug,
                "field": field,
                "label": (match or {}).get("display_name") or slug.replace("_", " ").title(),
                "has_key": bool(match and match["has_key"]),
                "env_name": (match or {}).get("api_key_env") or field.upper(),
                "requires_key": bool(match and match["requires_key"]),
            }
        )
    available = sum(1 for s in sources if s["available"])
    crawl_schedule = None
    crawl_sources = []
    if section == "crawl":
        crawl_schedule = load_crawl_schedule()
        crawl_sources = _crawl_source_rows()
        nxt = next_run_at(crawl_schedule)
        crawl_schedule["next_run_label"] = format_local(nxt) if nxt else "—"
        last = crawl_schedule.get("last_run") or ""
        try:
            from datetime import datetime

            last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00")) if last else None
        except ValueError:
            last_dt = None
        crawl_schedule["last_run_label"] = format_local(last_dt) if last_dt else "Never"
    return _ctx(
        request,
        config=cfg,
        root=str(ROOT_DIR),
        store=store_status().as_dict(),
        sources=sources,
        credentials=credentials,
        section=section,
        source_stats={
            "total": len(sources),
            "available": available,
            "disabled": sum(1 for s in sources if not s["enabled"]),
        },
        timezones=timezone_choices(cfg.timezone),
        now_local=now_local(cfg.timezone).strftime("%Y-%m-%d %H:%M:%S"),
        crawl_schedule=crawl_schedule,
        crawl_sources=crawl_sources,
        git=git_status() if section == "updates" else {"ok": False, "dirty": True, "error": ""},
        git_log=get_job_log(request, GIT_LOG_KEY) if section == "updates" else "",
        pm2=pm2_status() if section == "updates" else {"ok": False, "error": ""},
        pm2_log=get_job_log(request, PM2_LOG_KEY) if section == "updates" else "",
        activity=activity_payload()
        if section == "activity"
        else {"online": [], "online_count": 0, "events": [], "window_minutes": 5},
        audit_logs=list_audit_logs() if section == "audit" else [],
    )


def _safe_next(next_url: str | None, fallback: str) -> str:
    text = (next_url or "").strip()
    if text.startswith(("/settings", "/sources", "/library", "/search")):
        return text
    return fallback


def _settings_redirect(
    request: Request, section: str, message: str, level: str = "success", next_url: str | None = None
) -> RedirectResponse:
    set_flash(request, message, level)
    return RedirectResponse(_safe_next(next_url, f"/settings?section={section}"), status_code=303)


def _form_bool(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "on", "yes"}


def _mask(value: str) -> str:
    if not value:
        return "(not set)"
    if len(value) < 8:
        return "********"
    return value[:3] + "••••" + value[-2:]


def _format_bytes(value: int | None) -> str:
    if not value:
        return ""
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def existing_pdf_from_paper(paper: Paper):
    for row in sorted(paper.downloads or [], key=lambda item: item.id, reverse=True):
        path = safe_library_pdf(row.local_path)
        if path:
            return path
    return None
