"""Workspace, credentials, and operational settings."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.auth import (
    user_is_admin,
)
from app.database.settings_repository import (
    SettingsError,
    create_academic_source,
    delete_academic_source,
    save_crawl_schedule_settings,
    save_credential_settings,
    save_search_settings,
    save_workspace_settings,
    source_to_dict,
    toggle_academic_source,
    update_academic_source,
)
from app.services.library_reset import reset_library_repository
from app.services.missing_pdf_cleanup import cleanup_missing_pdf_records, delete_papers_without_local_pdf
from app.services.usage import record_usage
from app.utils.git_update import GitUpdateError, git_pull
from app.utils.pm2_control import Pm2Error, pm2_logs, pm2_restart
from app.web.dependencies import (
    _form_bool,
    _settings_ctx,
    _settings_redirect,
    templates,
)
from app.web.flash import GIT_LOG_KEY, PM2_LOG_KEY, set_job_log

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, section: str = "workspace"):
    allowed = {"workspace", "search", "crawl", "credentials", "sources", "updates", "activity", "audit"}
    if section not in allowed:
        section = "workspace"
    return templates.TemplateResponse(request, "settings.html", _settings_ctx(request, section))


@router.post("/settings/update")
def settings_git_pull(request: Request):
    try:
        result = git_pull()
        set_job_log(request, GIT_LOG_KEY, result.get("output") or "")
        if result.get("already_current"):
            return _settings_redirect(request, "updates", "Already up to date.")
        return _settings_redirect(request, "updates", "Pulled the latest code. Restart PM2 if Python files changed.")
    except GitUpdateError as exc:
        set_job_log(request, GIT_LOG_KEY, str(exc))
        return _settings_redirect(request, "updates", str(exc), "danger")
    except Exception as exc:
        set_job_log(request, GIT_LOG_KEY, str(exc))
        return _settings_redirect(request, "updates", "Git pull failed.", "danger")


@router.post("/settings/pm2/restart")
def settings_pm2_restart(request: Request):
    try:
        result = pm2_restart()
        set_job_log(request, PM2_LOG_KEY, result.get("output") or "")
        name = (result.get("status") or {}).get("name") or "researchpaper"
        return _settings_redirect(request, "updates", f"PM2 restarted {name}.")
    except Pm2Error as exc:
        set_job_log(request, PM2_LOG_KEY, str(exc))
        return _settings_redirect(request, "updates", str(exc), "danger")
    except Exception as exc:
        set_job_log(request, PM2_LOG_KEY, str(exc))
        return _settings_redirect(request, "updates", "PM2 restart failed.", "danger")


@router.post("/settings/pm2/logs")
def settings_pm2_logs(request: Request):
    try:
        result = pm2_logs()
        set_job_log(request, PM2_LOG_KEY, result.get("output") or "")
        return _settings_redirect(request, "updates", f"Loaded PM2 logs for {result.get('name', 'researchpaper')}.")
    except Pm2Error as exc:
        set_job_log(request, PM2_LOG_KEY, str(exc))
        return _settings_redirect(request, "updates", str(exc), "danger")
    except Exception as exc:
        set_job_log(request, PM2_LOG_KEY, str(exc))
        return _settings_redirect(request, "updates", "Could not load PM2 logs.", "danger")


@router.post("/settings/workspace")
def settings_save_workspace(
    request: Request,
    contact_email: str = Form(""),
    unpaywall_email: str = Form(""),
    library_dir: str = Form("research_library"),
    timezone: str = Form("UTC"),
    check_robots_txt: str | None = Form(None),
    prefer_https: str | None = Form(None),
    show_paywalled: str | None = Form(None),
):
    try:
        save_workspace_settings(
            {
                "contact_email": contact_email,
                "unpaywall_email": unpaywall_email,
                "library_dir": library_dir,
                "timezone": timezone,
                "check_robots_txt": _form_bool(check_robots_txt),
                "prefer_https": _form_bool(prefer_https),
                "show_paywalled": _form_bool(show_paywalled),
            }
        )
        return _settings_redirect(request, "workspace", "Workspace settings saved to MySQL.")
    except SettingsError as exc:
        return _settings_redirect(request, "workspace", str(exc), "danger")


@router.post("/settings/reset")
def settings_reset_repository(request: Request, confirm: str = Form("")):
    if not user_is_admin(request):
        return RedirectResponse("/", status_code=302)
    if confirm.strip().upper() != "RESET":
        return _settings_redirect(
            request,
            "workspace",
            "Reset cancelled — type RESET in the confirmation box.",
            "warning",
        )
    try:
        stats = reset_library_repository()
    except Exception as exc:
        return _settings_redirect(request, "workspace", f"Reset failed: {exc}", "danger")
    record_usage(
        request,
        "reset",
        f"{stats.papers} papers, {stats.search_jobs} search jobs, {stats.pdf_files_removed} PDFs",
    )
    return _settings_redirect(
        request,
        "workspace",
        (
            f"Library reset complete — removed {stats.papers} papers, "
            f"{stats.search_queries} searches, {stats.search_jobs} search jobs, "
            f"{stats.crawl_jobs} crawl jobs, and {stats.pdf_files_removed} PDF file(s)."
        ),
    )


@router.post("/settings/cleanup-missing-pdfs")
def settings_cleanup_missing_pdfs(request: Request, confirm: str = Form("")):
    if not user_is_admin(request):
        return RedirectResponse("/", status_code=302)
    if confirm.strip().upper() != "CLEANUP":
        return _settings_redirect(
            request,
            "workspace",
            "Cleanup cancelled — type CLEANUP in the confirmation box.",
            "warning",
        )
    try:
        stats = cleanup_missing_pdf_records()
    except Exception as exc:
        return _settings_redirect(request, "workspace", f"Cleanup failed: {exc}", "danger")
    record_usage(
        request,
        "cleanup",
        f"{stats.downloads_cleared} missing downloads, {stats.papers_updated} papers",
    )
    if not stats.downloads_cleared and not stats.papers_updated:
        return _settings_redirect(
            request,
            "workspace",
            "No missing PDF records found — every downloaded entry still has a file on disk.",
        )
    return _settings_redirect(
        request,
        "workspace",
        (
            f"Removed {stats.downloads_cleared} download record(s) whose PDF file is missing, "
            f"and updated {stats.papers_updated} paper(s) so they can be downloaded again."
        ),
    )


@router.post("/settings/delete-papers-without-pdf")
def settings_delete_papers_without_pdf(request: Request, confirm: str = Form("")):
    if not user_is_admin(request):
        return RedirectResponse("/", status_code=302)
    if confirm.strip().upper() != "DELETE":
        return _settings_redirect(
            request,
            "workspace",
            "Delete cancelled — type DELETE in the confirmation box.",
            "warning",
        )
    try:
        stats = delete_papers_without_local_pdf()
    except Exception as exc:
        return _settings_redirect(request, "workspace", f"Delete failed: {exc}", "danger")
    record_usage(request, "cleanup", f"{stats.papers_deleted} papers without local PDF")
    if not stats.papers_deleted:
        return _settings_redirect(
            request,
            "workspace",
            "No papers deleted — every library record already has a PDF file on this server.",
        )
    return _settings_redirect(
        request,
        "workspace",
        (
            f"Deleted {stats.papers_deleted} paper record(s) that did not have a PDF file "
            f"on this server. Papers with saved PDFs were kept."
        ),
    )


@router.post("/settings/search")
def settings_save_search(
    request: Request,
    download_limit: int = Form(0),
    default_max_results: int = Form(50),
    cfp_list_limit: int = Form(30),
    max_file_size: str = Form("150MB"),
    max_concurrent_requests: int = Form(5),
    max_concurrent_downloads: int = Form(3),
    request_timeout_seconds: float = Form(30),
    download_timeout_seconds: float = Form(120),
    max_redirects: int = Form(5),
):
    try:
        save_search_settings(
            {
                "download_limit": download_limit,
                "default_max_results": default_max_results,
                "cfp_list_limit": cfp_list_limit,
                "max_file_size": max_file_size,
                "max_concurrent_requests": max_concurrent_requests,
                "max_concurrent_downloads": max_concurrent_downloads,
                "request_timeout_seconds": request_timeout_seconds,
                "download_timeout_seconds": download_timeout_seconds,
                "max_redirects": max_redirects,
            }
        )
        return _settings_redirect(request, "search", "Search and download settings saved to MySQL.")
    except SettingsError as exc:
        return _settings_redirect(request, "search", str(exc), "danger")


@router.post("/settings/crawl")
def settings_save_crawl(
    request: Request,
    enabled: str | None = Form(None),
    interval_minutes: int = Form(60),
    sources: list[str] = Form(default=[]),
    query: str = Form(""),
    skip_existing: str | None = Form(None),
    open_access_only: str | None = Form(None),
    download: str | None = Form(None),
    pdfs_only: str | None = Form(None),
    max_pages: int = Form(5),
    max_papers: int = Form(500),
):
    try:
        save_crawl_schedule_settings(
            {
                "enabled": bool(enabled),
                "interval_minutes": interval_minutes,
                "sources": sources,
                "query": query,
                "skip_existing": skip_existing is not None,
                "open_access_only": bool(open_access_only),
                "download": bool(download),
                "pdfs_only": bool(pdfs_only),
                "max_pages": max_pages,
                "max_papers": max_papers,
            }
        )
        state = "enabled" if enabled else "disabled"
        return _settings_redirect(request, "crawl", f"Crawl schedule saved ({state}).")
    except SettingsError as exc:
        return _settings_redirect(request, "crawl", str(exc), "danger")


@router.post("/settings/credentials")
async def settings_save_credentials(request: Request):
    form = await request.form()
    data = {str(k): str(v) for k, v in form.items()}
    try:
        save_credential_settings(data)
        return _settings_redirect(request, "credentials", "API credentials saved to MySQL.")
    except SettingsError as exc:
        return _settings_redirect(request, "credentials", str(exc), "danger")


@router.post("/settings/sources")
def settings_create_source(
    request: Request,
    slug: str = Form(""),
    display_name: str = Form(...),
    description: str = Form(""),
    homepage_url: str = Form(""),
    api_base_url: str = Form(""),
    docs_url: str = Form(""),
    notes: str = Form(""),
    api_key: str = Form(""),
    api_key_env: str = Form(""),
    requests_per_second: float = Form(5),
    requests_per_second_with_key: str = Form(""),
    enabled: str | None = Form(None),
    requires_key: str | None = Form(None),
    next: str = Form(""),
):
    try:
        create_academic_source(
            {
                "slug": slug,
                "display_name": display_name,
                "description": description,
                "homepage_url": homepage_url,
                "api_base_url": api_base_url,
                "docs_url": docs_url,
                "notes": notes,
                "api_key": api_key,
                "api_key_env": api_key_env,
                "requests_per_second": requests_per_second,
                "requests_per_second_with_key": requests_per_second_with_key,
                "enabled": enabled,
                "requires_key": requires_key,
            }
        )
        return _settings_redirect(request, "sources", f"Added academic source “{display_name}”.", next_url=next)
    except SettingsError as exc:
        return _settings_redirect(request, "sources", str(exc), "danger", next_url=next)


@router.post("/settings/sources/{source_id}")
def settings_update_source(
    source_id: int,
    request: Request,
    display_name: str = Form(...),
    description: str = Form(""),
    homepage_url: str = Form(""),
    api_base_url: str = Form(""),
    docs_url: str = Form(""),
    notes: str = Form(""),
    api_key: str = Form(""),
    requests_per_second: float = Form(5),
    requests_per_second_with_key: str = Form(""),
    enabled: str | None = Form(None),
    requires_key: str | None = Form(None),
    clear_api_key: str | None = Form(None),
    next: str = Form(""),
):
    try:
        update_academic_source(
            source_id,
            {
                "display_name": display_name,
                "description": description,
                "homepage_url": homepage_url,
                "api_base_url": api_base_url,
                "docs_url": docs_url,
                "notes": notes,
                "api_key": api_key,
                "requests_per_second": requests_per_second,
                "requests_per_second_with_key": requests_per_second_with_key,
                "enabled": enabled,
                "requires_key": requires_key,
                "clear_api_key": clear_api_key,
            },
        )
        return _settings_redirect(request, "sources", "Academic source updated.", next_url=next)
    except SettingsError as exc:
        return _settings_redirect(request, "sources", str(exc), "danger", next_url=next)


@router.post("/settings/sources/{source_id}/delete")
def settings_delete_source(source_id: int, request: Request, next: str = Form("")):
    try:
        delete_academic_source(source_id)
        return _settings_redirect(request, "sources", "Academic source removed.", next_url=next)
    except SettingsError as exc:
        return _settings_redirect(request, "sources", str(exc), "danger", next_url=next)


@router.post("/settings/sources/{source_id}/toggle")
def settings_toggle_source(source_id: int, request: Request, next: str = Form("")):
    try:
        row = toggle_academic_source(source_id)
        wants_json = "application/json" in (request.headers.get("accept") or "")
        if wants_json:
            return {"ok": True, "source": source_to_dict(row)}
        state = "enabled" if row.enabled else "disabled"
        return _settings_redirect(request, "sources", f"{row.display_name} {state}.", next_url=next)
    except SettingsError as exc:
        if "application/json" in (request.headers.get("accept") or ""):
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return _settings_redirect(request, "sources", str(exc), "danger", next_url=next)
