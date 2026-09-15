"""Health, metrics, crawler, and host-system pages."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from app.auth import (
    current_user,
    user_is_admin,
)
from app.database.connection import session_scope
from app.database.repository import (
    active_crawl_job_any,
    active_crawl_job_for_user,
    crawl_job_is_scheduled,
    get_crawl_job,
)
from app.services.crawl_queue import cancel_crawl, crawl_progress_snapshot, enqueue_crawl
from app.services.crawl_queue import queue_snapshot as crawl_queue_snapshot
from app.services.crawl_service import filters_from_form
from app.services.progress import crawl_idle_snapshot
from app.services.system_health import collect_system_health
from app.services.usage import activity_payload, record_usage
from app.web.dependencies import (
    _crawl_source_lookup,
    _crawl_source_rows,
    _ctx,
    _request_user_id,
    templates,
)
from app.web.flash import set_flash

router = APIRouter()


@router.get("/health/live")
def health_live():
    return {"status": "ok"}


@router.get("/health/ready")
def health_ready():
    try:
        from sqlalchemy import text as sql_text

        with session_scope() as session:
            session.execute(sql_text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        return JSONResponse({"status": "degraded", "database": "unavailable"}, status_code=503)


@router.get("/metrics")
def metrics_endpoint(request: Request):
    user = current_user(request)
    if user is None:
        return JSONResponse({"ok": False, "error": "Sign in required"}, status_code=401)
    from app.observability.metrics import prometheus_text

    return PlainTextResponse(prometheus_text(), media_type="text/plain; version=0.0.4")


@router.get("/crawler", response_class=HTMLResponse)
def crawler_page(request: Request):
    if not user_is_admin(request):
        return RedirectResponse("/", status_code=302)
    user_id = _request_user_id(request)
    crawl_sources = _crawl_source_rows()
    crawlable_count = sum(1 for row in crawl_sources if row["crawlable"])
    job_id_param = request.query_params.get("job")
    with session_scope() as session:
        # Prefer this admin's job, then any running/pending (includes scheduled).
        focus_job = active_crawl_job_for_user(session, user_id) or active_crawl_job_any(session)
        if job_id_param:
            try:
                jid = int(job_id_param)
                row = get_crawl_job(session, jid)
                if row and row.status in ("pending", "running"):
                    focus_job = row
            except ValueError:
                pass
    job_progress = crawl_progress_snapshot(focus_job.id) if focus_job else crawl_idle_snapshot()
    if not job_progress:
        job_progress = crawl_idle_snapshot()
    if focus_job:
        job_progress.setdefault("query", focus_job.source)
        job_progress["scheduled"] = crawl_job_is_scheduled(focus_job)
    queue = crawl_queue_snapshot(user_id=user_id, is_admin=True)
    return templates.TemplateResponse(
        request,
        "crawler.html",
        _ctx(
            request,
            crawl_sources=crawl_sources,
            crawlable_count=crawlable_count,
            job=job_progress,
            active_job_id=focus_job.id if focus_job else None,
            crawl_queue=queue,
        ),
    )


@router.post("/crawler")
async def crawler_submit(
    request: Request,
    sources: list[str] = Form(default=[]),
    query: str = Form(""),
    year_from: str = Form(""),
    year_to: str = Form(""),
    page_size: int = Form(100),
    max_pages: int = Form(0),
    max_papers: int = Form(50000),
    skip_existing: str | None = Form(None),
    open_access_only: str | None = Form(None),
    pdfs_only: str | None = Form(None),
    download: str | None = Form(None),
):
    if not user_is_admin(request):
        return RedirectResponse("/", status_code=302)
    lookup = _crawl_source_lookup()
    selected = [slug.strip() for slug in sources if slug.strip()]
    if not selected:
        set_flash(request, "Select at least one source to crawl.", "warning")
        return RedirectResponse("/crawler", status_code=303)

    queued: list[str] = []
    skipped: list[str] = []
    job_ids: list[int] = []
    user_id = _request_user_id(request)
    for slug in selected:
        row = lookup.get(slug)
        if row is None:
            skipped.append(slug)
            continue
        if not row["crawlable"]:
            skipped.append(row["display_name"])
            continue
        filters = filters_from_form(
            source=slug,
            query=query,
            year_from=int(year_from) if year_from.strip() else None,
            year_to=int(year_to) if year_to.strip() else None,
            open_access_only=bool(open_access_only),
            skip_existing=skip_existing is not None,
            download=bool(download),
            pdfs_only=bool(pdfs_only),
            page_size=page_size,
            max_pages=max_pages,
            max_papers=max_papers,
        )
        job_ids.append(enqueue_crawl(user_id=user_id, filters=filters))
        queued.append(str(row["display_name"]))

    if not job_ids:
        set_flash(
            request,
            "No crawlable sources selected. Enable sources and pick ones that support paginated browse.",
            "warning",
        )
        return RedirectResponse("/crawler", status_code=303)

    record_usage(request, "crawl", ", ".join(queued))
    if len(job_ids) == 1:
        msg = f"Crawl queued (job #{job_ids[0]}) for {queued[0]}."
    else:
        msg = f"Queued {len(job_ids)} crawls: {', '.join(queued)}."
    if skipped:
        msg += f" Skipped {len(skipped)} unavailable or search-only source(s)."
    set_flash(request, msg, "info")
    return RedirectResponse(f"/crawler?live=1&job={job_ids[0]}", status_code=303)


@router.get("/system", response_class=HTMLResponse)
def system_health_page(request: Request):
    if not user_is_admin(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "system.html", _ctx(request))


@router.get("/api/system-health")
def system_health_api(request: Request):
    if not user_is_admin(request):
        return JSONResponse({"ok": False, "error": "Admin access required"}, status_code=403)
    payload = collect_system_health(process_limit=15)
    status = 200 if payload.get("ok") else 503
    return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("/api/crawl-progress")
def crawl_progress(request: Request, job_id: int | None = None):
    if not user_is_admin(request):
        return JSONResponse({"ok": False, "error": "Admin access required"}, status_code=403)
    user_id = _request_user_id(request)
    target_id = job_id
    scheduled = False
    if target_id is None:
        with session_scope() as session:
            active = active_crawl_job_for_user(session, user_id) if user_id is not None else None
            if active is None:
                active = active_crawl_job_any(session)
            if active:
                target_id = active.id
                scheduled = crawl_job_is_scheduled(active)
    if target_id is not None:
        snap = crawl_progress_snapshot(target_id)
        if snap:
            if not scheduled:
                with session_scope() as session:
                    row = get_crawl_job(session, target_id)
                    if row is not None:
                        scheduled = crawl_job_is_scheduled(row)
            snap["scheduled"] = scheduled
            return JSONResponse(snap, headers={"Cache-Control": "no-store"})
    return JSONResponse(crawl_idle_snapshot(), headers={"Cache-Control": "no-store"})


@router.get("/api/crawl-queue")
def crawl_queue_api(request: Request):
    if not user_is_admin(request):
        return JSONResponse({"ok": False, "error": "Admin access required"}, status_code=403)
    user_id = _request_user_id(request)
    return JSONResponse(
        crawl_queue_snapshot(user_id=user_id, is_admin=True),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/crawler/jobs/{job_id}/stop")
async def crawl_stop(request: Request, job_id: int):
    if not user_is_admin(request):
        return RedirectResponse("/", status_code=302)
    user_id = _request_user_id(request)
    accept = (request.headers.get("accept") or "").lower()
    wants_json = "application/json" in accept and "text/html" not in accept
    try:
        was = cancel_crawl(job_id, user_id=user_id, is_admin=True)
    except PermissionError as exc:
        if wants_json:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=403)
        set_flash(request, str(exc), "warning")
        return RedirectResponse("/crawler", status_code=303)
    except ValueError as exc:
        if wants_json:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        set_flash(request, str(exc), "warning")
        return RedirectResponse("/crawler", status_code=303)
    message = "Crawl stopped." if was == "pending" else "Stopping crawl…"
    set_flash(request, message, "info")
    if wants_json:
        return JSONResponse({"ok": True, "was": was, "message": message})
    return RedirectResponse(f"/crawler?live=1&job={job_id}", status_code=303)


@router.get("/api/activity")
def activity_api(request: Request):
    return JSONResponse(activity_payload(), headers={"Cache-Control": "no-store"})
