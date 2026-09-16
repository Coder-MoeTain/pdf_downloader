"""Dashboard, search, reports, and search-queue APIs."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.auth import (
    user_is_admin,
)
from app.config import get_runtime_config
from app.database.connection import retry_on_sqlite_lock, session_scope
from app.database.models import SearchQuery, User
from app.database.repository import (
    active_search_job_for_user,
    count_crawl_jobs,
    count_search_jobs,
    dashboard_stats,
    get_search_job,
    list_crawl_jobs,
    list_search_jobs,
)
from app.providers import provider_status
from app.services.progress import tracker
from app.services.project_service import list_user_projects, workspace_summary
from app.services.search_queue import cancel_search, enqueue_search, queue_snapshot, search_progress_snapshot
from app.services.search_service import filters_from_cli
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
    pagination_spec,
    share,
    status_meta,
)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    def _load() -> dict:
        with session_scope() as session:
            stats = dashboard_stats(session)
            user_id = _request_user_id(request)
            stats["workspace"] = (
                workspace_summary(session, user_id)
                if user_id
                else {
                    "projects": [],
                    "project_count": 0,
                    "screening_pending": 0,
                    "active": 0,
                }
            )
            from app.database.repository import list_saved_searches, list_upcoming_cfps

            stats["upcoming_cfps"] = list_upcoming_cfps(session, limit=5)
            stats["search_alerts"] = (
                [row for row in list_saved_searches(session, user_id) if row.alert_enabled or row.new_paper_count]
                if user_id
                else []
            )
            return stats

    try:
        stats = retry_on_sqlite_lock(_load)
    except OperationalError:
        stats = {
            "stored_total": 0,
            "total": 0,
            "downloadable": 0,
            "paywalled": 0,
            "oa": 0,
            "failed": 0,
            "searches": 0,
            "no_year": 0,
            "status_counts": {},
            "years": [],
            "publishers": [],
            "journals": [],
            "authors": [],
            "top_cited": [],
            "recent": [],
            "topics": [],
            "workspace": {"projects": [], "project_count": 0, "screening_pending": 0, "active": 0},
            "upcoming_cfps": [],
            "search_alerts": [],
        }

    total = int(stats["total"])
    stored_total = int(stats["stored_total"])
    downloadable = int(stats["downloadable"])
    paywalled = int(stats["paywalled"])
    oa = int(stats["oa"])
    failed = int(stats["failed"])
    searches = int(stats["searches"])
    year_counts = [
        {"year": row["year"], "yy": f"{int(row['year']) % 100:02d}", "count": row["count"]} for row in stats["years"]
    ]
    year_max = max((row["count"] for row in year_counts), default=0)
    year_chart = year_counts[-16:]
    for row in year_chart:
        row["bar"] = round((row["count"] / year_max) * 100, 1) if year_max else 0
    statuses = [
        {
            "code": code,
            "count": count,
            "pct": share(count, total),
            **status_meta(code),
        }
        for code, count in (stats.get("status_counts") or {}).items()
        if code
    ]
    statuses.sort(key=lambda row: (-row["count"], row["label"]))
    kpis = [
        {
            "href": "/library",
            "label": "Papers in library",
            "value": total,
            "tone": "primary",
            "hint": f"{searches} search{'es' if searches != 1 else ''} run",
        },
        {
            "href": "/library?pdf=1",
            "label": "PDFs on server",
            "value": downloadable,
            "tone": "success",
            "hint": "Files saved in the library folder",
        },
        {
            "href": "/library?status=PAYWALLED",
            "label": "Paywalled",
            "value": paywalled,
            "tone": "warning",
            "hint": f"{share(paywalled, stored_total)}% of library",
        },
        {
            "href": "/downloads?status=FAILED",
            "label": "Failed downloads",
            "value": failed,
            "tone": "danger",
            "hint": "Retry from Downloads",
        },
        {
            "href": "/library?oa=1",
            "label": "Open access",
            "value": oa,
            "tone": "secondary",
            "hint": "OA metadata / link",
        },
    ]
    recent = stats["recent"]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        _ctx(
            request,
            total=total,
            kpis=kpis,
            year_chart=year_chart,
            years=year_counts,
            no_year=int(stats["no_year"]),
            statuses=statuses,
            publishers=[
                {"name": row["name"], "count": row["count"], "pct": share(row["count"], total)}
                for row in stats["publishers"]
            ],
            journals=[
                {"name": row["name"], "count": row["count"], "pct": share(row["count"], total)}
                for row in stats["journals"]
            ],
            authors=[
                {"name": row["name"], "count": row["count"], "pct": share(row["count"], total)}
                for row in stats["authors"]
            ],
            top_cited=stats["top_cited"],
            recent=recent,
            latest_search=recent[0] if recent else None,
            topics=stats["topics"],
            searches=searches,
            downloadable=downloadable,
            failed=failed,
            stored_total=stored_total,
            workspace=stats.get("workspace")
            or {"projects": [], "project_count": 0, "screening_pending": 0, "active": 0},
            upcoming_cfps=stats.get("upcoming_cfps") or [],
            search_alerts=stats.get("search_alerts") or [],
        ),
    )


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request):
    cfg = get_runtime_config()
    user_id = _request_user_id(request)
    is_admin = user_is_admin(request)
    job_id_param = request.query_params.get("job")
    with session_scope() as session:
        recent = session.scalars(select(SearchQuery).order_by(SearchQuery.created_at.desc()).limit(8)).all()
        active_job = active_search_job_for_user(session, user_id)
        focus_job = active_job
        saved_searches = []
        latest_papers: list = []
        user_projects = []
        from app.database.repository import list_saved_searches, query_library

        if user_id is not None:
            from app.services.alert_service import alert_preview

            saved_searches = list_saved_searches(session, user_id)
            for row in saved_searches:
                row.alert_preview = alert_preview(row)
            user_projects = list_user_projects(session, user_id)
        if focus_job is None and job_id_param:
            try:
                jid = int(job_id_param)
                row = get_search_job(session, jid)
                if row and row.status in ("pending", "running"):
                    if is_admin or row.user_id == user_id:
                        focus_job = row
            except ValueError:
                pass
        latest_search = recent[0] if recent else None
        if latest_search is not None:
            latest_papers, _total = query_library(
                session,
                latest_search_id=latest_search.id,
                rater_user_id=user_id,
                limit=20,
            )
        available = [row for row in provider_status() if row.get("available")]
        job_progress = search_progress_snapshot(focus_job.id) if focus_job else tracker.snapshot()
        queue = queue_snapshot(user_id=user_id, is_admin=is_admin)
        return templates.TemplateResponse(
            request,
            "search.html",
            _ctx(
                request,
                config=cfg,
                recent_searches=recent,
                available_sources=available,
                job=job_progress or tracker.snapshot(),
                active_job_id=focus_job.id if focus_job else None,
                search_queue=queue,
                topics=cfg.topics,
                saved_searches=saved_searches,
                user_projects=user_projects,
                latest_papers=latest_papers,
            ),
        )


@router.get("/discover", response_class=HTMLResponse)
def discover_page(request: Request):
    return search_page(request)


@router.post("/search")
async def search_submit(
    request: Request,
    query: str = Form(...),
    year_from: str = Form(""),
    year_to: str = Form(""),
    max_results: int = Form(50),
    open_access_only: str | None = Form(None),
    download: str | None = Form(None),
    sort: str = Form("relevance"),
    source: str = Form(""),
):
    filters = filters_from_cli(
        query,
        year_from=int(year_from) if year_from.strip() else None,
        year_to=int(year_to) if year_to.strip() else None,
        max_results=max_results,
        open_access_only=bool(open_access_only),
        no_download=not bool(download),
        sort=sort,
        source=source.strip() or None,
    )
    user_id = _request_user_id(request)
    job_id = enqueue_search(user_id=user_id, query=query.strip(), filters=filters)
    record_usage(request, "search", query.strip())
    set_flash(
        request,
        f"Search queued (job #{job_id}). Sources and PDF downloads run in parallel — watch the live log.",
        "info",
    )
    return RedirectResponse(f"/discover?live=1&job={job_id}", status_code=303)


@router.post("/search/save")
def search_save(
    request: Request,
    query: str = Form(...),
    name: str = Form(""),
    year_from: str = Form(""),
    year_to: str = Form(""),
    max_results: int = Form(50),
    open_access_only: str | None = Form(None),
    sort: str = Form("relevance"),
    source: str = Form(""),
    alert_enabled: str | None = Form(None),
    alert_frequency: str = Form("weekly"),
):
    user_id = _request_user_id(request)
    if user_id is None:
        return RedirectResponse("/login?next=/search", status_code=302)
    from app.database.repository import save_search_config

    filters = {
        "year_from": int(year_from) if year_from.strip() else None,
        "year_to": int(year_to) if year_to.strip() else None,
        "max_results": max_results,
        "open_access_only": bool(open_access_only),
        "sort": sort,
        "source": source.strip() or None,
    }
    with session_scope() as session:
        saved = save_search_config(
            session,
            user_id,
            name,
            query,
            filters,
            alert_enabled=bool(alert_enabled),
            alert_frequency=alert_frequency,
        )
    set_flash(request, f"Saved search “{saved.name}”.", "success")
    return RedirectResponse("/search", status_code=303)


@router.post("/search/saved/{search_id}/alert")
def search_alert_update(
    request: Request,
    search_id: int,
    alert_enabled: str | None = Form(None),
    alert_frequency: str = Form("weekly"),
):
    user_id = _request_user_id(request)
    if user_id is None:
        return RedirectResponse("/login?next=/search", status_code=302)
    from app.database.repository import update_saved_search_alert

    with session_scope() as session:
        row = update_saved_search_alert(
            session, user_id, search_id, enabled=bool(alert_enabled), frequency=alert_frequency
        )
    if row is None:
        set_flash(request, "Saved search not found.", "danger")
    else:
        set_flash(request, "Alert settings updated. Alerts never download PDFs automatically.", "success")
    return RedirectResponse("/search", status_code=303)


@router.post("/search/saved/{search_id}/run-alert")
async def search_alert_run(request: Request, search_id: int):
    user_id = _request_user_id(request)
    if user_id is None:
        return RedirectResponse("/login?next=/search", status_code=302)
    from app.database.repository import get_saved_search
    from app.services.alert_service import run_saved_search_alert

    with session_scope() as session:
        row = get_saved_search(session, user_id, search_id)
        if row is None:
            set_flash(request, "Saved search not found.", "danger")
            return RedirectResponse("/search", status_code=303)
    result = await run_saved_search_alert(search_id)
    if not result.get("ok"):
        set_flash(request, result.get("error") or "Alert check failed.", "danger")
    else:
        set_flash(
            request,
            f"Alert check found {result.get('new_count', 0)} new paper(s). No PDFs were downloaded.",
            "info",
        )
    return RedirectResponse("/search", status_code=303)


@router.post("/search/jobs/{job_id}/stop")
async def search_stop(request: Request, job_id: int):
    user_id = _request_user_id(request)
    accept = (request.headers.get("accept") or "").lower()
    wants_json = "application/json" in accept and "text/html" not in accept
    try:
        was = cancel_search(job_id, user_id=user_id, is_admin=user_is_admin(request))
    except PermissionError as exc:
        if wants_json:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=403)
        set_flash(request, str(exc), "warning")
        return RedirectResponse("/search", status_code=303)
    except ValueError as exc:
        if wants_json:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        set_flash(request, str(exc), "warning")
        return RedirectResponse("/search", status_code=303)
    message = "Search stopped." if was == "pending" else "Stopping search…"
    set_flash(request, message, "info")
    if wants_json:
        return JSONResponse({"ok": True, "was": was, "message": message})
    return RedirectResponse("/search?live=1", status_code=303)


@router.get("/api/search-progress")
def search_progress(request: Request, job_id: int | None = None):
    user_id = _request_user_id(request)
    target_id = job_id
    if target_id is None and user_id is not None:
        with session_scope() as session:
            active = active_search_job_for_user(session, user_id)
            if active:
                target_id = active.id
    if target_id is not None:
        snap = search_progress_snapshot(target_id)
        if snap:
            return JSONResponse(snap, headers={"Cache-Control": "no-store"})
    return JSONResponse(tracker.snapshot(), headers={"Cache-Control": "no-store"})


@router.get("/api/search-queue")
def search_queue_api(request: Request):
    user_id = _request_user_id(request)
    is_admin = user_is_admin(request)
    return JSONResponse(
        queue_snapshot(user_id=user_id, is_admin=is_admin),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/reports", response_class=HTMLResponse)
def reports_page(
    request: Request,
    tab: str = "search",
    status: str = "",
    q: str = "",
    user: QueryInt = 0,
    page: QueryPage = 1,
    per_page: QueryInt = DEFAULT_PAGE_SIZE,
):
    tab = (tab or "search").strip().lower()
    if tab not in {"search", "crawl"}:
        tab = "search"
    is_admin = user_is_admin(request)
    if tab == "crawl" and not is_admin:
        tab = "search"
    status = status.strip().lower()
    q = q.strip()
    per_page = clamp_page_size(per_page)
    filter_user_id = user if user and user > 0 else 0
    request_uid = _request_user_id(request)
    # Regular users only see their own search jobs; admins can filter any user.
    scoped_user_id: int | None
    if is_admin:
        scoped_user_id = filter_user_id or None
    else:
        scoped_user_id = request_uid
    statuses = (status,) if status else None
    status_chips = [
        {"code": "completed", "label": "Completed"},
        {"code": "running", "label": "Running"},
        {"code": "pending", "label": "Queued"},
        {"code": "failed", "label": "Failed"},
        {"code": "cancelled", "label": "Stopped"},
    ]
    with session_scope() as session:
        if tab == "crawl":
            total = count_crawl_jobs(session, user_id=scoped_user_id, statuses=statuses, q=q or None)
            pager = pagination_spec(max(page, 1), total, per_page)
            rows = list_crawl_jobs(
                session,
                user_id=scoped_user_id,
                statuses=statuses,
                q=q or None,
                limit=per_page,
                offset=(pager["page"] - 1) * per_page,
                with_user=True,
            )
        else:
            total = count_search_jobs(session, user_id=scoped_user_id, statuses=statuses, q=q or None)
            pager = pagination_spec(max(page, 1), total, per_page)
            rows = list_search_jobs(
                session,
                user_id=scoped_user_id,
                statuses=statuses,
                q=q or None,
                limit=per_page,
                offset=(pager["page"] - 1) * per_page,
                with_user=True,
            )
        user_options: list[dict] = []
        if is_admin:
            members = session.scalars(select(User).order_by(User.name, User.email)).all()
            user_options = [
                {"id": member.id, "name": (member.name or member.email or f"User {member.id}").strip()}
                for member in members
            ]
    filters_state = {
        "tab": tab,
        "status": status,
        "q": q,
        "user": filter_user_id,
        "per_page": per_page,
    }
    return templates.TemplateResponse(
        request,
        "reports.html",
        _ctx(
            request,
            tab=tab,
            rows=rows,
            pager=pager,
            q=q,
            status=status,
            filter_user=filter_user_id,
            user_options=user_options,
            status_chips=status_chips,
            filters=filters_state,
            per_page=per_page,
            page_sizes=PAGE_SIZES,
        ),
    )
