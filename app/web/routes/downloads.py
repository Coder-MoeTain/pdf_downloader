"""PDF download, preview, and download-queue routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import selectinload

from app.database.connection import session_scope
from app.database.models import Download, Paper, SearchQuery
from app.database.repository import (
    download_user_options,
    visible_download_clauses,
)
from app.services.download_queue import enqueue_oa_download, enqueue_resume_downloads, oa_download_active
from app.services.download_service import (
    DownloadError,
    ensure_local_pdf,
    existing_pdf_path,
    stop_downloads,
)
from app.services.progress import download_tracker
from app.services.usage import record_usage
from app.web.dependencies import (
    _ctx,
    _request_user_id,
    templates,
)
from app.web.flash import set_flash
from app.web.ui import (
    DEFAULT_PAGE_SIZE,
    PAGE_SIZES,
    QueryInt,
    QueryPage,
    clamp_page_size,
    ordered_status_counts,
    pagination_spec,
)

router = APIRouter()


@router.get("/papers/{paper_id}/pdf")
async def download_paper_pdf(request: Request, paper_id: int):
    """Fetch or serve a PDF without blocking uvicorn's event loop.

    `ensure_local_pdf` does sync SQLite + disk I/O; running it on the main loop
    freezes every other page for the duration of the download.
    """
    user_id = _request_user_id(request)
    try:
        path = await ensure_local_pdf(paper_id, topic_slug="library", user_id=user_id)
        record_usage(request, "download", path.name)
    except DownloadError as exc:
        set_flash(request, str(exc), "warning")
        return RedirectResponse("/library?latest=1", status_code=303)
    return FileResponse(
        path=str(path),
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="attachment",
    )


@router.post("/download-oa")
async def download_oa(request: Request, latest: str | None = Form(None)):
    search_id = None
    if latest:
        with session_scope() as session:
            row = session.scalar(select(SearchQuery).order_by(SearchQuery.id.desc()).limit(1))
            if row:
                search_id = row.id
    user_id = _request_user_id(request)
    record_usage(request, "download", "Open-access PDF batch")
    if oa_download_active():
        set_flash(request, "A PDF download is already running. Watch progress on the Downloads page.", "info")
    elif enqueue_oa_download(search_id=search_id, user_id=user_id):
        set_flash(
            request,
            "Downloading legally available PDFs. This can take a few minutes — "
            "you can browse other pages while downloads continue in the background.",
            "info",
        )
    else:
        set_flash(request, "Could not start the download queue. Try again in a moment.", "warning")
    return RedirectResponse("/downloads", status_code=303)


@router.post("/downloads/stop")
async def downloads_stop(request: Request, paper_id: int | None = Form(None)):
    accept = (request.headers.get("accept") or "").lower()
    wants_json = "application/json" in accept and "text/html" not in accept
    if paper_id:
        from app.database.repository import mark_downloading_stopped

        with session_scope() as session:
            cleared = mark_downloading_stopped(session, paper_id=paper_id, error="Stopped by user")
        result = {"ok": True, "was_active": False, "cleared": cleared, "paper_id": paper_id}
        message = "Download stopped." if cleared else "That paper was not downloading."
        level = "info" if cleared else "warning"
    else:
        result = stop_downloads(clear_stuck=True)
        if result.get("was_active"):
            message = "Stopping downloads…"
            level = "info"
        elif result.get("cleared"):
            message = f"Stopped {result['cleared']} stuck download(s). Use Resume to try again."
            level = "info"
        else:
            message = "No downloads were running."
            level = "warning"
    if wants_json:
        return JSONResponse({**result, "message": message}, headers={"Cache-Control": "no-store"})
    set_flash(request, message, level)
    return RedirectResponse("/downloads", status_code=303)


@router.post("/downloads/resume")
async def downloads_resume(request: Request):
    accept = (request.headers.get("accept") or "").lower()
    wants_json = "application/json" in accept and "text/html" not in accept
    user_id = _request_user_id(request)
    if oa_download_active():
        message = "A PDF download is already running. Stop it first, or wait for it to finish."
        level = "warning"
        ok = False
    elif enqueue_resume_downloads(user_id=user_id, limit=0):
        message = "Resuming stuck downloads. Watch the live log on this page."
        level = "info"
        ok = True
        record_usage(request, "download", "Resume stuck downloads")
    else:
        message = "Could not start resume. Try again in a moment."
        level = "warning"
        ok = False
    if wants_json:
        return JSONResponse({"ok": ok, "message": message}, headers={"Cache-Control": "no-store"})
    set_flash(request, message, level)
    return RedirectResponse("/downloads", status_code=303)


@router.get("/papers/{paper_id}/preview")
def preview_paper_pdf(paper_id: int):
    with session_scope() as session:
        paper = session.scalar(select(Paper).options(selectinload(Paper.downloads)).where(Paper.id == paper_id))
        if paper is None:
            return HTMLResponse("Paper not found", status_code=404)
        path = existing_pdf_path(paper)
    if path is None:
        return HTMLResponse("No downloaded PDF is available to preview.", status_code=404)
    return FileResponse(
        path=str(path),
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "private, max-age=120",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/download-progress")
def download_progress():
    return JSONResponse(download_tracker.snapshot(), headers={"Cache-Control": "no-store"})


@router.get("/downloads", response_class=HTMLResponse)
def downloads_page(
    request: Request,
    status: str = "",
    q: str = "",
    user: QueryInt = 0,
    page: QueryPage = 1,
    per_page: QueryInt = DEFAULT_PAGE_SIZE,
):
    status = status.strip()
    q = q.strip()
    per_page = clamp_page_size(per_page)
    user_id = user if user and user > 0 else 0
    status_order = case(
        (Download.status == "DOWNLOADING", 0),
        (Download.status == "DOWNLOADED", 1),
        (Download.status == "FAILED", 2),
        else_=3,
    )
    search_like = f"%{q}%" if q else ""
    with session_scope() as session:
        hide = visible_download_clauses(status=status)
        filters = [clause for clause in hide]
        if status:
            filters.append(Download.status == status)
        if q:
            filters.append(or_(Paper.title.ilike(search_like), Paper.doi.ilike(search_like)))
        if user_id:
            filters.append(Download.downloaded_by_user_id == user_id)
        count_stmt = select(func.count(Download.id)).join(Paper, Download.paper_id == Paper.id)
        for clause in filters:
            count_stmt = count_stmt.where(clause)
        total = session.scalar(count_stmt) or 0
        pager = pagination_spec(max(page, 1), total, per_page)
        stmt = (
            select(Download)
            .join(Paper, Download.paper_id == Paper.id)
            .options(selectinload(Download.paper), selectinload(Download.downloaded_by))
            .order_by(status_order, Download.id.desc())
        )
        for clause in filters:
            stmt = stmt.where(clause)
        rows = list(session.scalars(stmt.offset((pager["page"] - 1) * per_page).limit(per_page)).all())
        chip_stmt = (
            select(Download.status, func.count(Download.id))
            .join(Paper, Download.paper_id == Paper.id)
            .group_by(Download.status)
        )
        for clause in visible_download_clauses(status=""):
            chip_stmt = chip_stmt.where(clause)
        if q:
            chip_stmt = chip_stmt.where(or_(Paper.title.ilike(search_like), Paper.doi.ilike(search_like)))
        if user_id:
            chip_stmt = chip_stmt.where(Download.downloaded_by_user_id == user_id)
        counts = dict(session.execute(chip_stmt).all())
        user_options = download_user_options(session, include_id=user_id or None)
    filters_state = {"status": status, "q": q, "user": user_id, "per_page": per_page}
    user_label = next((item["name"] for item in user_options if item["id"] == user_id), "")
    has_filters = bool(status or q or user_id)
    return templates.TemplateResponse(
        request,
        "downloads.html",
        _ctx(
            request,
            rows=rows,
            counts=counts,
            status_chips=ordered_status_counts(counts),
            status=status,
            q=q,
            filter_user=user_id,
            user_label=user_label,
            user_options=user_options,
            per_page=per_page,
            pager=pager,
            filters=filters_state,
            page_sizes=PAGE_SIZES,
            has_filters=has_filters,
        ),
    )
