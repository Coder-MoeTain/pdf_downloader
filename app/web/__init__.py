"""Local FastAPI dashboard for ResearchPaper Collector."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from app import __app_name__, __version__
from app.config import ROOT_DIR, load_config
from app.database.connection import init_db, session_scope
from app.database.models import Author, Download, Paper, PaperAuthor, SearchQuery, SearchResult
from app.database.repository import apply_paper_filters, downloadable_clause, library_search, set_paper_rating
from app.providers import provider_status
from app.services.download_service import (
    DownloadError,
    download_open_access_papers,
    ensure_local_pdf,
    existing_pdf_path,
    pdf_button_state,
    safe_library_pdf,
)
from app.services.progress import tracker
from app.services.search_service import SearchService, filters_from_cli
from app.utils.logger import setup_logging
from app.web.ui import active_page, status_meta

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
templates.env.globals["pdf_button_state"] = pdf_button_state
templates.env.globals["status_meta"] = status_meta
templates.env.globals["can_preview"] = lambda paper: existing_pdf_path(paper) is not None
templates.env.filters["filesize"] = lambda value: _format_bytes(value)

app = FastAPI(title=__app_name__, version=__version__)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

_search_message = {"text": "", "level": "info"}


@app.on_event("startup")
def _startup() -> None:
    setup_logging()
    init_db()


def _ctx(request: Request, **extra):
    cfg = load_config()
    payload = {
        "request": request,
        "app_name": cfg.name,
        "version": cfg.version,
        "flash": _search_message,
        "page": active_page(request.url.path),
        "progress": tracker.snapshot(),
    }
    payload.update(extra)
    payload["page"] = active_page(request.url.path)
    return payload


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with session_scope() as session:
        total = session.scalar(select(func.count(Paper.id))) or 0
        oa = session.scalar(select(func.count(Paper.id)).where(Paper.open_access.is_(True))) or 0
        downloadable = session.scalar(select(func.count(Paper.id)).where(downloadable_clause())) or 0
        downloaded = session.scalar(select(func.count(Download.id)).where(Download.status == "DOWNLOADED")) or 0
        paywalled = session.scalar(select(func.count(Paper.id)).where(Paper.status == "PAYWALLED")) or 0
        failed = session.scalar(select(func.count(Download.id)).where(Download.status == "FAILED")) or 0
        searches = session.scalar(select(func.count(SearchQuery.id))) or 0
        years = session.execute(
            select(Paper.publication_year, func.count(Paper.id))
            .where(Paper.publication_year.is_not(None))
            .group_by(Paper.publication_year)
            .order_by(Paper.publication_year)
        ).all()
        publishers = session.execute(
            select(Paper.publisher, func.count(Paper.id))
            .where(Paper.publisher.is_not(None))
            .group_by(Paper.publisher)
            .order_by(func.count(Paper.id).desc())
            .limit(8)
        ).all()
        journals = session.execute(
            select(Paper.journal, func.count(Paper.id))
            .where(Paper.journal.is_not(None))
            .group_by(Paper.journal)
            .order_by(func.count(Paper.id).desc())
            .limit(8)
        ).all()
        top_cited = session.scalars(
            select(Paper).where(Paper.citation_count.is_not(None)).order_by(Paper.citation_count.desc()).limit(8)
        ).all()
        recent = session.scalars(select(SearchQuery).order_by(SearchQuery.created_at.desc()).limit(6)).all()
        topics = Counter()
        for q in session.scalars(select(SearchQuery)).all():
            topics[q.original_query] += 1
    return templates.TemplateResponse(
        "dashboard.html",
        _ctx(
            request,
            total=total,
            oa=oa,
            downloadable=downloadable,
            downloaded=downloaded,
            paywalled=paywalled,
            failed=failed,
            searches=searches,
            years=[{"year": y, "count": c} for y, c in years],
            publishers=publishers,
            journals=journals,
            top_cited=top_cited,
            recent=recent,
            topics=topics.most_common(10),
        ),
    )


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request):
    return templates.TemplateResponse("search.html", _ctx(request))


@app.post("/search")
async def search_submit(
    background_tasks: BackgroundTasks,
    query: str = Form(...),
    year_from: str = Form(""),
    year_to: str = Form(""),
    max_results: int = Form(50),
    open_access_only: str | None = Form(None),
    download: str | None = Form(None),
    sort: str = Form("relevance"),
):
    filters = filters_from_cli(
        query,
        year_from=int(year_from) if year_from.strip() else None,
        year_to=int(year_to) if year_to.strip() else None,
        max_results=max_results,
        open_access_only=bool(open_access_only),
        no_download=not bool(download),
        sort=sort,
    )
    _search_message["text"] = f"Search running for “{query}”. This can take a few minutes."
    _search_message["level"] = "info"

    async def _job():
        try:
            stats = await SearchService().run(filters)
            _search_message["text"] = (
                f"Finished “{query}”: {stats.unique_papers} unique papers, "
                f"{stats.pdfs_downloaded} PDFs downloaded."
            )
            _search_message["level"] = "success"
        except Exception as exc:
            _search_message["text"] = f"Search failed: {exc}"
            _search_message["level"] = "danger"

    background_tasks.add_task(_job)
    return RedirectResponse("/library?latest=1", status_code=303)


@app.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    q: str = "",
    status: str = "",
    page: int = 1,
    latest: int = 0,
    pdf: int = 0,
    min_rating: int = 0,
):
    page = max(page, 1)
    per_page = 25
    downloadable = bool(pdf)
    min_rating = max(0, min(min_rating, 5))
    latest_search = None
    oa_pending = 0
    with session_scope() as session:
        latest_search = session.scalar(select(SearchQuery).order_by(SearchQuery.id.desc()).limit(1))
        if q:
            papers = library_search(
                session,
                q,
                limit=200,
                status=status,
                downloadable=downloadable,
                min_rating=min_rating,
            )
            total = len(papers)
            papers = papers[(page - 1) * per_page : page * per_page]
        elif latest and latest_search:
            stmt = (
                select(Paper)
                .join(SearchResult, SearchResult.paper_id == Paper.id)
                .where(SearchResult.search_query_id == latest_search.id)
                .options(selectinload(Paper.downloads))
                .order_by(SearchResult.rank)
            )
            count_stmt = (
                select(func.count(Paper.id))
                .join(SearchResult, SearchResult.paper_id == Paper.id)
                .where(SearchResult.search_query_id == latest_search.id)
            )
            stmt = apply_paper_filters(stmt, status=status, downloadable=downloadable, min_rating=min_rating)
            count_stmt = apply_paper_filters(
                count_stmt, status=status, downloadable=downloadable, min_rating=min_rating
            )
            total = session.scalar(count_stmt) or 0
            papers = session.scalars(stmt.offset((page - 1) * per_page).limit(per_page)).unique().all()
            oa_pending = session.scalar(
                select(func.count(Paper.id))
                .join(SearchResult, SearchResult.paper_id == Paper.id)
                .where(
                    SearchResult.search_query_id == latest_search.id,
                    Paper.pdf_url.is_not(None),
                    Paper.status.in_(["OA_AVAILABLE", "FOUND", "FAILED"]),
                )
            ) or 0
        else:
            stmt = select(Paper).options(selectinload(Paper.downloads)).order_by(Paper.relevance_score.desc())
            count_stmt = select(func.count(Paper.id))
            stmt = apply_paper_filters(stmt, status=status, downloadable=downloadable, min_rating=min_rating)
            count_stmt = apply_paper_filters(
                count_stmt, status=status, downloadable=downloadable, min_rating=min_rating
            )
            total = session.scalar(count_stmt) or 0
            papers = session.scalars(stmt.offset((page - 1) * per_page).limit(per_page)).all()
            oa_pending = session.scalar(
                select(func.count(Paper.id)).where(
                    Paper.pdf_url.is_not(None),
                    Paper.status.in_(["OA_AVAILABLE", "FOUND", "FAILED"]),
                )
            ) or 0
    return templates.TemplateResponse(
        "library.html",
        _ctx(
            request,
            papers=papers,
            q=q,
            status=status,
            pdf=downloadable,
            min_rating=min_rating,
            page_num=page,
            total=total,
            per_page=per_page,
            latest=bool(latest),
            latest_search=latest_search,
            oa_pending=oa_pending,
            search_running=bool(latest_search and latest_search.status == "running"),
        ),
    )


@app.post("/api/papers/{paper_id}/rating")
async def rate_paper(paper_id: int, request: Request):
    try:
        payload = await request.json()
        rating = int(payload.get("rating", -1))
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid rating payload"}, status_code=400)
    if rating not in range(0, 6):
        return JSONResponse({"ok": False, "error": "Rating must be 0–5"}, status_code=400)
    with session_scope() as session:
        paper = set_paper_rating(session, paper_id, rating)
        if paper is None:
            return JSONResponse({"ok": False, "error": "Paper not found"}, status_code=404)
        saved = paper.user_rating
    return JSONResponse({"ok": True, "rating": saved or 0})


@app.get("/papers/{paper_id}/pdf")
async def download_paper_pdf(paper_id: int):
    try:
        path = await ensure_local_pdf(paper_id, topic_slug="library")
    except DownloadError as exc:
        _search_message["text"] = str(exc)
        _search_message["level"] = "warning"
        return RedirectResponse("/library?latest=1", status_code=303)
    return FileResponse(
        path=str(path),
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="attachment",
    )


@app.post("/download-oa")
async def download_oa(background_tasks: BackgroundTasks, latest: str | None = Form(None)):
    search_id = None
    if latest:
        with session_scope() as session:
            row = session.scalar(select(SearchQuery).order_by(SearchQuery.id.desc()).limit(1))
            if row:
                search_id = row.id
    _search_message["text"] = "Downloading legally available PDFs. This can take a few minutes."
    _search_message["level"] = "info"

    async def _job():
        try:
            stats = await download_open_access_papers(search_id=search_id)
            _search_message["text"] = (
                f"PDF download finished: {stats['downloaded']} saved, "
                f"{stats['failed']} failed, {stats['skipped']} skipped."
            )
            _search_message["level"] = "success"
        except Exception as exc:
            _search_message["text"] = f"PDF download failed: {exc}"
            _search_message["level"] = "danger"

    background_tasks.add_task(_job)
    return RedirectResponse("/downloads", status_code=303)


@app.get("/papers/{paper_id}/preview")
def preview_paper_pdf(paper_id: int):
    with session_scope() as session:
        paper = session.scalar(
            select(Paper).options(selectinload(Paper.downloads)).where(Paper.id == paper_id)
        )
        if paper is None:
            return HTMLResponse("Paper not found", status_code=404)
        path = existing_pdf_from_paper(paper)
    if path is None:
        _search_message["text"] = "No downloaded PDF is available to preview."
        _search_message["level"] = "warning"
        return RedirectResponse("/downloads", status_code=303)
    return FileResponse(
        path=str(path),
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="inline",
    )


@app.get("/api/download-progress")
def download_progress():
    return JSONResponse(tracker.snapshot())


@app.get("/downloads", response_class=HTMLResponse)
def downloads_page(request: Request, status: str = ""):
    status_order = case(
        (Download.status == "DOWNLOADING", 0),
        (Download.status == "DOWNLOADED", 1),
        (Download.status == "FAILED", 2),
        else_=3,
    )
    with session_scope() as session:
        stmt = (
            select(Download)
            .options(selectinload(Download.paper))
            .order_by(status_order, Download.id.desc())
        )
        if status:
            stmt = stmt.where(Download.status == status)
        rows = session.scalars(stmt.limit(200)).all()
        counts = dict(
            session.execute(select(Download.status, func.count(Download.id)).group_by(Download.status)).all()
        )
    return templates.TemplateResponse(
        "downloads.html",
        _ctx(request, rows=rows, counts=counts, status=status, progress=tracker.snapshot()),
    )


@app.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request):
    return templates.TemplateResponse("sources.html", _ctx(request, providers=provider_status()))


@app.get("/statistics", response_class=HTMLResponse)
def statistics_page(request: Request):
    with session_scope() as session:
        years = session.execute(
            select(Paper.publication_year, func.count(Paper.id))
            .where(Paper.publication_year.is_not(None))
            .group_by(Paper.publication_year)
            .order_by(Paper.publication_year)
        ).all()
        publishers = session.execute(
            select(Paper.publisher, func.count(Paper.id))
            .where(Paper.publisher.is_not(None))
            .group_by(Paper.publisher)
            .order_by(func.count(Paper.id).desc())
            .limit(15)
        ).all()
        journals = session.execute(
            select(Paper.journal, func.count(Paper.id))
            .where(Paper.journal.is_not(None))
            .group_by(Paper.journal)
            .order_by(func.count(Paper.id).desc())
            .limit(15)
        ).all()
        authors = session.execute(
            select(Author.name, func.count(PaperAuthor.id))
            .join(PaperAuthor, PaperAuthor.author_id == Author.id)
            .group_by(Author.name)
            .order_by(func.count(PaperAuthor.id).desc())
            .limit(15)
        ).all()
        statuses = session.execute(select(Paper.status, func.count(Paper.id)).group_by(Paper.status)).all()
    return templates.TemplateResponse(
        "statistics.html",
        _ctx(
            request,
            years=[{"year": y, "count": c} for y, c in years],
            publishers=[{"name": n, "count": c} for n, c in publishers],
            journals=[{"name": n, "count": c} for n, c in journals],
            authors=[{"name": n, "count": c} for n, c in authors],
            statuses=[{"code": s, "count": c} for s, c in statuses],
        ),
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    cfg = load_config()
    env = cfg.env
    masked = {
        "CONTACT_EMAIL": env.contact_email,
        "UNPAYWALL_EMAIL": env.unpaywall_email or env.contact_email,
        "SEMANTIC_SCHOLAR_API_KEY": _mask(env.semantic_scholar_api_key),
        "CORE_API_KEY": _mask(env.core_api_key),
        "SPRINGER_API_KEY": _mask(env.springer_api_key),
        "ELSEVIER_API_KEY": _mask(env.elsevier_api_key),
        "IEEE_API_KEY": _mask(env.ieee_api_key),
        "NCBI_API_KEY": _mask(env.ncbi_api_key),
        "NASA_ADS_TOKEN": _mask(env.nasa_ads_token),
    }
    return templates.TemplateResponse(
        "settings.html",
        _ctx(request, env=masked, config=cfg, root=str(ROOT_DIR)),
    )


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
