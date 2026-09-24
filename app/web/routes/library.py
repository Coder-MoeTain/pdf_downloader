"""Research library listing and paper metadata actions."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.auth import (
    user_is_admin,
)
from app.database.connection import retry_on_sqlite_lock, session_scope
from app.database.models import Paper, SearchQuery, SearchResult
from app.database.repository import (
    delete_library_paper,
    download_user_options,
    library_facets,
    query_library,
    set_paper_rating,
)
from app.models.paper import PaperStatus
from app.services.download_queue import enqueue_oa_recheck, oa_download_active
from app.services.download_service import (
    safe_library_pdf,
)
from app.web.dependencies import (
    _ctx,
    _request_user_id,
    _safe_next,
    _source_rows,
    templates,
)
from app.web.flash import set_flash
from app.web.ui import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SORT,
    PAGE_SIZES,
    SORT_OPTIONS,
    QueryInt,
    QueryPage,
    clamp_page_size,
    library_href,
    library_status_panel,
    pagination_spec,
)

router = APIRouter()


@router.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    q: str = "",
    status: str = "",
    page: QueryPage = 1,
    latest: QueryInt = 0,
    pdf: QueryInt = 0,
    oa: QueryInt = 0,
    access: str = "",
    min_rating: QueryInt = 0,
    category: str = "",
    year: QueryInt = 0,
    source: str = "",
    journal: str = "",
    user: QueryInt = 0,
    sort: str = DEFAULT_SORT,
    per_page: QueryInt = DEFAULT_PAGE_SIZE,
):
    status = status.strip().upper()
    access = access.strip().lower()
    if access == "open":
        oa = 1
        pdf = 0
        status = ""
    elif access == "paywalled":
        status = PaperStatus.PAYWALLED.value
        oa = 0
        pdf = 0
    elif access == "downloadable":
        pdf = 1
        oa = 0
        status = ""
    downloadable = bool(pdf)
    open_access = bool(oa)
    # Access chips are exclusive: prefer the most specific requested filter.
    if status == PaperStatus.PAYWALLED.value:
        downloadable = False
        open_access = False
    elif open_access:
        status = ""
        downloadable = False
    elif downloadable:
        status = ""
    min_rating = max(0, min(min_rating, 5))
    category = category.strip()
    source = source.strip()
    journal = journal.strip()
    sort = sort if sort in {key for key, _label in SORT_OPTIONS} else DEFAULT_SORT
    per_page = clamp_page_size(per_page)
    year = year if year and year > 0 else 0
    user_id = user if user and user > 0 else 0
    latest_search = None
    papers = []
    total = 0
    oa_pending = 0
    user_options = []
    use_latest = False
    pager = pagination_spec(max(page, 1), 1, per_page)

    def _load_library() -> None:
        nonlocal latest_search, papers, total, oa_pending, user_options, use_latest, pager
        with session_scope() as session:
            latest_search = session.scalar(select(SearchQuery).order_by(SearchQuery.id.desc()).limit(1))
            use_latest = bool(latest) and latest_search is not None
            requested_page = max(page, 1)
            query_kwargs = dict(
                q=q,
                status=status,
                downloadable=downloadable,
                open_access=open_access,
                min_rating=min_rating,
                category=category,
                year=year or None,
                source=source,
                journal=journal,
                user_id=user_id or None,
                sort=sort,
                latest_search_id=latest_search.id if use_latest else None,
                limit=per_page,
            )
            papers, total = query_library(
                session,
                offset=(requested_page - 1) * per_page,
                **query_kwargs,
            )
            pager = pagination_spec(requested_page, total, per_page)
            # Rare: requested page past the end — refetch the clamped page only.
            if pager["page"] != requested_page and pager["page"] >= 1:
                papers, total = query_library(
                    session,
                    offset=(pager["page"] - 1) * per_page,
                    **query_kwargs,
                )
            oa_where = [
                Paper.pdf_url.is_not(None),
                Paper.status.in_(["OA_AVAILABLE", "FOUND", "FAILED"]),
            ]
            oa_stmt = select(func.count(Paper.id)).where(*oa_where)
            if use_latest:
                oa_stmt = (
                    select(func.count(Paper.id))
                    .join(SearchResult, SearchResult.paper_id == Paper.id)
                    .where(SearchResult.search_query_id == latest_search.id, *oa_where)
                )
            oa_pending = session.scalar(oa_stmt) or 0
            # User filter UI is unused on the library page; skip the join unless filtering.
            if user_id:
                user_options = download_user_options(session, include_id=user_id)
            else:
                user_options = []

    try:
        retry_on_sqlite_lock(_load_library)
    except OperationalError:
        papers, total, oa_pending, user_options = [], 0, 0, []
        pager = pagination_spec(1, 0, per_page)

    account_id = _request_user_id(request)
    remarks: dict[int, str] = {}
    if account_id is not None and papers:
        from app.database.repository import user_paper_remarks_map

        with session_scope() as session:
            remarks = user_paper_remarks_map(session, account_id, [p.id for p in papers])

    def _load_facets() -> dict:
        with session_scope() as session:
            return library_facets(session, light=True)

    try:
        facets = retry_on_sqlite_lock(_load_facets)
    except OperationalError:
        facets = {
            "categories": [],
            "years": [],
            "sources": [],
            "journals": [],
            "status_counts": {},
            "visible_total": 0,
            "downloadable": 0,
            "downloaded": 0,
            "open_access": 0,
            "paywalled": 0,
        }
    if category and not any(item["name"] == category for item in facets["categories"]):
        peak = facets["categories"][0]["count"] if facets["categories"] else total or 1
        facets["categories"].insert(
            0,
            {
                "name": category,
                "count": total,
                "pct": round((total / peak) * 100, 1) if peak else 100,
            },
        )
    filters = {
        "q": q,
        "status": status,
        "pdf": downloadable,
        "oa": open_access,
        "min_rating": min_rating,
        "latest": use_latest,
        "category": category,
        "year": year,
        "source": source,
        "journal": journal,
        "user": user_id,
        "sort": sort,
        "per_page": per_page,
    }
    user_label = next((item["name"] for item in user_options if item["id"] == user_id), "")
    has_filters = bool(
        q or status or downloadable or open_access or min_rating or category or year or source or journal or user_id
    )
    stats = library_status_panel(facets)
    base_filters = {"latest": use_latest, "sort": sort, "per_page": per_page}
    kpis = [
        {
            "href": library_href(base_filters),
            "label": "Papers",
            "value": stats["visible_total"],
            "tone": "primary",
            "hint": "Visible in library",
            "active": not has_filters,
        },
        {
            "href": library_href(base_filters, pdf=True, oa=False, status="", page=1),
            "label": "On server",
            "value": stats["downloadable"],
            "tone": "success",
            "hint": "PDF file saved locally",
            "active": bool(downloadable) and not open_access and status != PaperStatus.PAYWALLED.value,
        },
        {
            "href": library_href(base_filters, oa=True, pdf=False, status="", page=1),
            "label": "Open access",
            "value": stats["open_access"],
            "tone": "secondary",
            "hint": "OA metadata / link",
            "active": bool(open_access),
        },
        {
            "href": library_href(base_filters, status=PaperStatus.PAYWALLED.value, pdf=False, oa=False, page=1),
            "label": "Paywalled",
            "value": stats["paywalled"],
            "tone": "warning",
            "hint": "Metadata only",
            "active": status == PaperStatus.PAYWALLED.value,
        },
    ]
    return templates.TemplateResponse(
        request,
        "library.html",
        _ctx(
            request,
            papers=papers,
            q=q,
            status=status,
            pdf=downloadable,
            oa=open_access,
            min_rating=min_rating,
            category=category,
            year=year,
            source=source,
            journal=journal,
            filter_user=user_id,
            user_label=user_label,
            user_options=user_options,
            sort=sort,
            page_num=pager["page"],
            total=total,
            per_page=per_page,
            pager=pager,
            filters=filters,
            facets=facets,
            sort_options=SORT_OPTIONS,
            page_sizes=PAGE_SIZES,
            has_filters=has_filters,
            latest=use_latest,
            latest_search=latest_search,
            oa_pending=oa_pending,
            oa_recheck_pending=int(facets.get("paywalled") or 0),
            search_running=bool(use_latest and latest_search and latest_search.status == "running"),
            kpis=kpis,
            has_library_stats=stats["has_stats"],
            source_catalog={row["slug"]: row for row in _source_rows()},
            remarks=remarks,
        ),
    )


@router.post("/library/recheck-oa")
def library_recheck_oa(request: Request):
    user_id = _request_user_id(request)
    if oa_download_active():
        set_flash(request, "A PDF download is already running. Watch progress on the Downloads page.", "info")
    elif enqueue_oa_recheck(user_id=user_id):
        set_flash(
            request,
            "Re-checking Unpaywall for paywalled papers. If a legal PDF exists, it will download in the background.",
            "info",
        )
    else:
        set_flash(request, "Could not start the Unpaywall re-check. Try again in a moment.", "warning")
    return RedirectResponse("/downloads", status_code=303)


@router.post("/papers/{paper_id}/recheck-oa")
def paper_recheck_oa(request: Request, paper_id: int):
    user_id = _request_user_id(request)
    if oa_download_active():
        set_flash(request, "A PDF download is already running. Watch progress on the Downloads page.", "info")
    elif enqueue_oa_recheck(user_id=user_id, paper_id=paper_id):
        set_flash(
            request,
            "Re-checking Unpaywall for this paper. If a legal PDF exists, it will download in the background.",
            "info",
        )
    else:
        set_flash(request, "Could not start the Unpaywall re-check. Try again in a moment.", "warning")
    return RedirectResponse("/downloads", status_code=303)


@router.post("/api/papers/{paper_id}/rating")
async def rate_paper(paper_id: int, request: Request):
    """Save or clear a 1–5 user rating for a paper."""
    try:
        payload = await request.json()
        rating = int(payload.get("rating", -1))
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid rating payload"}, status_code=400)
    if rating not in range(0, 6):
        return JSONResponse({"ok": False, "error": "Rating must be 0–5"}, status_code=400)
    user_id = _request_user_id(request)
    with session_scope() as session:
        paper = set_paper_rating(session, paper_id, rating, user_id=user_id)
        if paper is None:
            return JSONResponse({"ok": False, "error": "Paper not found"}, status_code=404)
        saved = paper.user_rating
        if user_id:
            from app.database.models import UserPaper

            row = session.scalar(select(UserPaper).where(UserPaper.user_id == user_id, UserPaper.paper_id == paper_id))
            if row is not None:
                saved = row.rating
    return JSONResponse({"ok": True, "rating": saved or 0})


@router.post("/papers/{paper_id}/delete")
def library_delete_paper(request: Request, paper_id: int, next: str = Form("")):
    if not user_is_admin(request):
        set_flash(request, "Only admins can delete papers from the library.", "warning")
        return RedirectResponse(_safe_next(next, "/library"), status_code=303)
    try:
        with session_scope() as session:
            title, paths = delete_library_paper(session, paper_id)
        for path_value in paths:
            dest = safe_library_pdf(path_value)
            if dest and dest.is_file():
                dest.unlink()
        set_flash(request, f"Deleted “{title}” from the library.", "success")
    except ValueError as exc:
        set_flash(request, str(exc), "danger")
    return RedirectResponse(_safe_next(next, "/library"), status_code=303)


@router.get("/api/papers/{paper_id}/workspace")
def paper_workspace(paper_id: int, request: Request, for_user_id: int | None = None):
    user_id = _request_user_id(request)
    if user_id is None:
        return JSONResponse({"ok": False, "error": "Sign in required"}, status_code=401)
    from app.database.repository import list_user_collections, related_papers, user_paper_workspace

    target_user_id = user_id
    if for_user_id and for_user_id > 0 and for_user_id != user_id:
        if not user_is_admin(request):
            return JSONResponse({"ok": False, "error": "Admin access required"}, status_code=403)
        target_user_id = for_user_id
    with session_scope() as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            return JSONResponse({"ok": False, "error": "Paper not found"}, status_code=404)
        workspace = user_paper_workspace(session, target_user_id, paper_id)
        collections = [
            {"id": row.id, "name": row.name, "slug": row.slug, "selected": row.id in workspace["collection_ids"]}
            for row in list_user_collections(session, target_user_id)
        ]
        related = [
            {"id": row.id, "title": row.title, "year": row.publication_year, "citations": row.citation_count or 0}
            for row in related_papers(session, paper_id)
        ]
    return JSONResponse(
        {
            "ok": True,
            "paper_id": paper_id,
            "for_user_id": target_user_id,
            **workspace,
            "collections": collections,
            "related": related,
        }
    )


@router.post("/papers/{paper_id}/notes")
def paper_save_notes(
    paper_id: int,
    request: Request,
    notes: str = Form(""),
    tags: str = Form(""),
    next: str = Form(""),
    for_user_id: int | None = Form(None),
    save_tags: str | None = Form(None),
):
    user_id = _request_user_id(request)
    if user_id is None:
        return RedirectResponse("/login?next=/library", status_code=302)
    from app.database.repository import set_user_paper_notes, set_user_paper_tags, split_tags

    target_user_id = user_id
    if for_user_id and int(for_user_id) > 0 and int(for_user_id) != user_id:
        if not user_is_admin(request):
            set_flash(request, "Only admins can save remarks for another account.", "warning")
            return RedirectResponse(_safe_next(next, "/library"), status_code=303)
        target_user_id = int(for_user_id)

    with session_scope() as session:
        if session.get(Paper, paper_id) is None:
            set_flash(request, "Paper not found.", "danger")
            return RedirectResponse(_safe_next(next, "/library"), status_code=303)
        set_user_paper_notes(session, target_user_id, paper_id, notes)
        if save_tags:
            set_user_paper_tags(session, target_user_id, paper_id, split_tags(tags))
    set_flash(request, "Remark saved.", "success")
    return RedirectResponse(_safe_next(next, "/library"), status_code=303)


@router.post("/papers/{paper_id}/reading-status")
def paper_save_status(paper_id: int, request: Request, reading_status: str = Form("unread"), next: str = Form("")):
    user_id = _request_user_id(request)
    if user_id is None:
        return RedirectResponse("/login?next=/library", status_code=302)
    from app.database.repository import set_reading_status

    try:
        with session_scope() as session:
            set_reading_status(session, user_id, paper_id, reading_status)
    except ValueError as exc:
        set_flash(request, str(exc), "danger")
        return RedirectResponse(_safe_next(next, "/library"), status_code=303)
    set_flash(request, "Reading status updated.", "success")
    return RedirectResponse(_safe_next(next, "/library"), status_code=303)


@router.post("/papers/{paper_id}/collections")
def paper_save_collection(paper_id: int, request: Request, collection_id: int = Form(...), next: str = Form("")):
    user_id = _request_user_id(request)
    if user_id is None:
        return RedirectResponse("/login?next=/library", status_code=302)
    from app.database.repository import add_paper_to_collection

    try:
        with session_scope() as session:
            add_paper_to_collection(session, user_id, collection_id, paper_id)
    except ValueError as exc:
        set_flash(request, str(exc), "danger")
        return RedirectResponse(_safe_next(next, "/library"), status_code=303)
    set_flash(request, "Added to collection.", "success")
    return RedirectResponse(_safe_next(next, "/library"), status_code=303)
