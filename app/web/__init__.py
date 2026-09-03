"""Local FastAPI dashboard for ResearchPaper Collector."""

from __future__ import annotations

from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import selectinload

from app import __app_name__, __version__
from app.auth import (
    PASSWORD_MIN_LENGTH,
    ROLE_ADMIN,
    ROLE_USER,
    authenticate_local,
    auth_required,
    create_local_user,
    current_user,
    get_oauth,
    google_login_enabled,
    hash_password,
    is_admin_path,
    is_public_path,
    list_users,
    safe_next_path,
    session_secret,
    set_user_role,
    upsert_google_user,
    user_count,
    user_is_admin,
    user_role,
    user_to_session,
    verify_password,
)
from app.config import ROOT_DIR, get_runtime_config
from app.database.connection import init_db, session_scope
from app.database.models import Author, Download, Paper, PaperAuthor, SearchQuery, SearchResult, User
from app.database.repository import (
    downloadable_clause,
    library_facets,
    query_library,
    set_paper_rating,
    split_tags,
    visible_download_clauses,
    visible_paper_clauses,
)
from app.database.settings_repository import (
    SettingsError,
    create_academic_source,
    delete_academic_source,
    get_academic_source,
    list_academic_sources,
    save_credential_settings,
    save_search_settings,
    save_workspace_settings,
    seed_academic_sources,
    source_to_dict,
    toggle_academic_source,
    update_academic_source,
)
from app.database.settings_store import store_status
from app.database.source_catalog import BUILTIN_SOURCES, SOURCE_KEY_FIELDS
from app.providers import PROVIDER_CLASSES, provider_status
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
from app.utils.time import format_local, now_local, timezone_abbrev, timezone_choices, timezone_offset_label
from app.web.ui import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SORT,
    PAGE_SIZES,
    SORT_OPTIONS,
    active_page,
    clamp_page_size,
    download_actor_name,
    downloads_href,
    is_new_download,
    library_href,
    ordered_status_counts,
    pagination_spec,
    paper_abstract_meta,
    paper_downloader_name,
    share,
    source_label,
    status_meta,
)

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
templates.env.globals["pdf_button_state"] = pdf_button_state
templates.env.globals["status_meta"] = status_meta
templates.env.globals["can_preview"] = lambda paper: existing_pdf_path(paper) is not None
templates.env.globals["library_href"] = library_href
templates.env.globals["downloads_href"] = downloads_href
templates.env.globals["source_label"] = source_label
templates.env.globals["paper_abstract_meta"] = paper_abstract_meta
templates.env.globals["paper_downloader_name"] = paper_downloader_name
templates.env.globals["download_actor_name"] = download_actor_name
templates.env.globals["is_new_download"] = is_new_download
templates.env.filters["filesize"] = lambda value: _format_bytes(value)
templates.env.filters["localdt"] = lambda value, fmt="%Y-%m-%d %H:%M": format_local(value, fmt)
templates.env.filters["tags"] = split_tags

app = FastAPI(title=__app_name__, version=__version__)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

_search_message = {"text": "", "level": "info"}


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    path = request.url.path
    if is_public_path(path):
        return await call_next(request)
    user = current_user(request)
    needs_login = auth_required() or path.startswith("/account")
    if needs_login and user is None:
        if path.startswith("/api/"):
            return JSONResponse({"ok": False, "error": "Sign in required"}, status_code=401)
        nxt = path
        if request.url.query:
            nxt = f"{path}?{request.url.query}"
        return RedirectResponse(f"/login?next={nxt}", status_code=302)
    if user and is_admin_path(path) and user_role(user) != ROLE_ADMIN:
        if path.startswith("/api/"):
            return JSONResponse({"ok": False, "error": "Admin access required"}, status_code=403)
        _search_message["text"] = "Sources and Settings are limited to admin accounts."
        _search_message["level"] = "warning"
        return RedirectResponse("/", status_code=302)
    return await call_next(request)


app.add_middleware(SessionMiddleware, secret_key=session_secret(), same_site="lax", https_only=False)


@app.on_event("startup")
def _startup() -> None:
    setup_logging()
    init_db()


def _login_ctx(request: Request, next_url: str = "/") -> dict:
    return _ctx(
        request,
        next_url=safe_next_path(next_url),
        google_ready=google_login_enabled(),
        allow_register=user_count() == 0,
        password_min=PASSWORD_MIN_LENGTH,
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if current_user(request):
        return RedirectResponse(safe_next_path(next), status_code=302)
    return templates.TemplateResponse(request, "login.html", _login_ctx(request, next))


@app.post("/login")
def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    name: str = Form(""),
    next: str = Form("/"),
):
    nxt = safe_next_path(next)
    email = (email or "").strip()
    if not email or not password:
        _search_message["text"] = "Enter your email and password."
        _search_message["level"] = "warning"
        return templates.TemplateResponse(request, "login.html", _login_ctx(request, nxt), status_code=400)
    try:
        with session_scope() as session:
            if user_count() == 0:
                row = create_local_user(session, email=email, password=password, name=name, role=ROLE_ADMIN)
            else:
                row = authenticate_local(session, email, password)
                if row is None:
                    _search_message["text"] = "Email or password is not correct."
                    _search_message["level"] = "danger"
                    return templates.TemplateResponse(request, "login.html", _login_ctx(request, nxt), status_code=401)
            request.session["user"] = user_to_session(row)
            signed_email = row.email
            is_admin = user_role(request.session["user"]) == ROLE_ADMIN
    except ValueError as exc:
        _search_message["text"] = str(exc)
        _search_message["level"] = "danger"
        return templates.TemplateResponse(request, "login.html", _login_ctx(request, nxt), status_code=400)
    if is_admin_path(nxt) and not is_admin:
        nxt = "/"
    _search_message["text"] = f"Signed in as {signed_email}."
    _search_message["level"] = "success"
    return RedirectResponse(nxt, status_code=303)


@app.get("/auth/google")
async def auth_google(request: Request, next: str = "/"):
    if not google_login_enabled():
        _search_message["text"] = "Google sign-in is not configured. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env."
        _search_message["level"] = "warning"
        return RedirectResponse("/login", status_code=302)
    request.session["oauth_next"] = safe_next_path(next)
    redirect_uri = str(request.url_for("auth_google_callback"))
    return await get_oauth().google.authorize_redirect(request, redirect_uri)


@app.get("/auth/google/callback", name="auth_google_callback")
async def auth_google_callback(request: Request):
    nxt = safe_next_path(request.session.pop("oauth_next", "/"))
    try:
        token = await get_oauth().google.authorize_access_token(request)
    except Exception:
        _search_message["text"] = "Google sign-in failed. Try again."
        _search_message["level"] = "danger"
        return RedirectResponse("/login", status_code=302)
    info = token.get("userinfo") or {}
    email = str(info.get("email") or "").strip().lower()
    google_id = str(info.get("sub") or "").strip()
    if not email or not google_id:
        _search_message["text"] = "Google did not return an email address for this account."
        _search_message["level"] = "danger"
        return RedirectResponse("/login", status_code=302)
    with session_scope() as session:
        row = upsert_google_user(
            session,
            google_id=google_id,
            email=email,
            name=str(info.get("name") or ""),
            picture=str(info.get("picture") or "") or None,
        )
        request.session["user"] = user_to_session(row)
        is_admin = bool(row.is_admin)
    if is_admin_path(nxt) and not is_admin:
        nxt = "/"
    _search_message["text"] = f"Signed in as {email}."
    _search_message["level"] = "success"
    return RedirectResponse(nxt, status_code=302)


@app.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request):
    request.session.clear()
    _search_message["text"] = "Signed out."
    _search_message["level"] = "info"
    return RedirectResponse("/login", status_code=303)


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


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login?next=/account", status_code=302)
    with session_scope() as session:
        members = _account_users(session) if user_role(user) == ROLE_ADMIN else []
    return templates.TemplateResponse(
        request,
        "account.html",
        _ctx(request, members=members, password_min=PASSWORD_MIN_LENGTH, roles=(ROLE_USER, ROLE_ADMIN)),
    )


@app.post("/account/profile")
def account_save_profile(request: Request, name: str = Form("")):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login?next=/account", status_code=302)
    with session_scope() as session:
        row = session.get(User, user["id"])
        if row is None:
            request.session.clear()
            return RedirectResponse("/login", status_code=302)
        row.name = (name or "").strip() or row.email.split("@")[0]
        session.flush()
        request.session["user"] = user_to_session(row)
    _search_message["text"] = "Profile saved."
    _search_message["level"] = "success"
    return RedirectResponse("/account", status_code=303)


@app.post("/account/password")
def account_save_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login?next=/account", status_code=302)
    if new_password != confirm_password:
        _search_message["text"] = "New password and confirmation do not match."
        _search_message["level"] = "danger"
        return RedirectResponse("/account", status_code=303)
    if len(new_password) < PASSWORD_MIN_LENGTH:
        _search_message["text"] = f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
        _search_message["level"] = "danger"
        return RedirectResponse("/account", status_code=303)
    with session_scope() as session:
        row = session.get(User, user["id"])
        if row is None:
            request.session.clear()
            return RedirectResponse("/login", status_code=302)
        if row.password_hash and not verify_password(current_password, row.password_hash):
            _search_message["text"] = "Current password is not correct."
            _search_message["level"] = "danger"
            return RedirectResponse("/account", status_code=303)
        row.password_hash = hash_password(new_password)
        session.flush()
        request.session["user"] = user_to_session(row)
    _search_message["text"] = "Password updated."
    _search_message["level"] = "success"
    return RedirectResponse("/account", status_code=303)


@app.post("/account/users")
def account_add_user(
    request: Request,
    email: str = Form(""),
    name: str = Form(""),
    password: str = Form(""),
    role: str = Form(ROLE_USER),
):
    user = current_user(request)
    if user is None or user_role(user) != ROLE_ADMIN:
        return RedirectResponse("/", status_code=302)
    try:
        with session_scope() as session:
            create_local_user(session, email=email, password=password, name=name, role=role)
    except ValueError as exc:
        _search_message["text"] = str(exc)
        _search_message["level"] = "danger"
        return RedirectResponse("/account", status_code=303)
    _search_message["text"] = f"Added {email.strip().lower()} as {role}."
    _search_message["level"] = "success"
    return RedirectResponse("/account", status_code=303)


@app.post("/account/users/{user_id}/role")
def account_set_role(request: Request, user_id: int, role: str = Form(ROLE_USER)):
    user = current_user(request)
    if user is None or user_role(user) != ROLE_ADMIN:
        return RedirectResponse("/", status_code=302)
    try:
        with session_scope() as session:
            row = set_user_role(session, user_id, role)
            if row.id == user.get("id"):
                request.session["user"] = user_to_session(row)
    except ValueError as exc:
        _search_message["text"] = str(exc)
        _search_message["level"] = "danger"
        return RedirectResponse("/account", status_code=303)
    _search_message["text"] = "Role updated."
    _search_message["level"] = "success"
    return RedirectResponse("/account", status_code=303)


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
    payload = {
        "request": request,
        "app_name": cfg.name,
        "version": __version__,
        "flash": _search_message,
        "page": active_page(request.url.path),
        "progress": tracker.snapshot(),
        "timezone": cfg.timezone,
        "timezone_abbrev": timezone_abbrev(cfg.timezone),
        "timezone_offset": timezone_offset_label(cfg.timezone),
        "show_paywalled": cfg.show_paywalled,
        "user": current_user(request),
        "is_admin": user_is_admin(request),
        "auth_enabled": True,
        "auth_required": auth_required(),
        "google_ready": google_login_enabled(),
        "role": user_role(current_user(request)) if current_user(request) else ("admin" if not auth_required() else "user"),
    }
    payload.update(extra)
    payload["page"] = active_page(request.url.path)
    return payload


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    visible = visible_paper_clauses()
    with session_scope() as session:
        stored_total = session.scalar(select(func.count(Paper.id))) or 0
        total = session.scalar(select(func.count(Paper.id)).where(*visible)) or 0
        oa = session.scalar(select(func.count(Paper.id)).where(Paper.open_access.is_(True), *visible)) or 0
        downloadable = session.scalar(select(func.count(Paper.id)).where(downloadable_clause(), *visible)) or 0
        downloaded = session.scalar(select(func.count(Download.id)).where(Download.status == "DOWNLOADED")) or 0
        paywalled = session.scalar(select(func.count(Paper.id)).where(Paper.status == "PAYWALLED")) or 0
        failed = session.scalar(select(func.count(Download.id)).where(Download.status == "FAILED")) or 0
        searches = session.scalar(select(func.count(SearchQuery.id))) or 0
        no_year = session.scalar(
            select(func.count(Paper.id)).where(Paper.publication_year.is_(None), *visible)
        ) or 0
        years = session.execute(
            select(Paper.publication_year, func.count(Paper.id))
            .where(Paper.publication_year.is_not(None), *visible)
            .group_by(Paper.publication_year)
            .order_by(Paper.publication_year)
        ).all()
        publishers = session.execute(
            select(Paper.publisher, func.count(Paper.id))
            .where(Paper.publisher.is_not(None), Paper.publisher != "", *visible)
            .group_by(Paper.publisher)
            .order_by(func.count(Paper.id).desc())
            .limit(6)
        ).all()
        journals = session.execute(
            select(Paper.journal, func.count(Paper.id))
            .where(Paper.journal.is_not(None), Paper.journal != "", *visible)
            .group_by(Paper.journal)
            .order_by(func.count(Paper.id).desc())
            .limit(6)
        ).all()
        status_rows = session.execute(
            select(Paper.status, func.count(Paper.id)).where(*visible).group_by(Paper.status)
        ).all()
        top_cited = session.scalars(
            select(Paper)
            .where(Paper.citation_count.is_not(None), *visible)
            .order_by(Paper.citation_count.desc())
            .limit(6)
        ).all()
        recent = session.scalars(select(SearchQuery).order_by(SearchQuery.created_at.desc()).limit(6)).all()
        topics = session.execute(
            select(SearchQuery.original_query, func.count(SearchQuery.id))
            .group_by(SearchQuery.original_query)
            .order_by(func.count(SearchQuery.id).desc())
            .limit(6)
        ).all()

    year_counts = [
        {"year": year, "yy": f"{year % 100:02d}", "count": count}
        for year, count in years
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
        for code, count in status_rows
        if code
    ]
    statuses.sort(key=lambda row: (-row["count"], row["label"]))
    kpis = [
        {"href": "/library", "label": "Papers in library", "value": total, "tone": "primary", "hint": f"{searches} search{'es' if searches != 1 else ''} run"},
        {"href": "/library?pdf=1", "label": "Downloadable PDFs", "value": downloadable, "tone": "success", "hint": f"{share(downloadable, total)}% of library"},
        {"href": "/downloads?status=DOWNLOADED", "label": "Downloaded", "value": downloaded, "tone": "info", "hint": "Saved to disk"},
        {"href": "/library?status=PAYWALLED", "label": "Paywalled", "value": paywalled, "tone": "warning", "hint": f"{share(paywalled, stored_total)}% of library"},
        {"href": "/downloads?status=FAILED", "label": "Failed downloads", "value": failed, "tone": "danger", "hint": "Retry from Downloads"},
        {"href": "/library?status=OA_AVAILABLE", "label": "Open access", "value": oa, "tone": "secondary", "hint": f"{share(oa, total)}% of library"},
    ]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        _ctx(
            request,
            total=total,
            kpis=kpis,
            year_chart=year_chart,
            years=year_counts,
            no_year=no_year,
            statuses=statuses,
            publishers=[{"name": name, "count": count, "pct": share(count, total)} for name, count in publishers],
            journals=[{"name": name, "count": count, "pct": share(count, total)} for name, count in journals],
            top_cited=top_cited,
            recent=recent,
            latest_search=recent[0] if recent else None,
            topics=[{"name": name, "count": count} for name, count in topics],
            searches=searches,
            downloadable=downloadable,
            failed=failed,
            stored_total=stored_total,
        ),
    )


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request):
    cfg = get_runtime_config()
    with session_scope() as session:
        recent = session.scalars(select(SearchQuery).order_by(SearchQuery.created_at.desc()).limit(8)).all()
    available = [row for row in provider_status() if row.get("available")]
    return templates.TemplateResponse(
        request,
        "search.html",
        _ctx(
            request,
            config=cfg,
            recent_searches=recent,
            available_sources=available,
            job=tracker.snapshot(),
            topics=cfg.topics[:6],
        ),
    )


@app.post("/search")
async def search_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    query: str = Form(...),
    year_from: str = Form(""),
    year_to: str = Form(""),
    max_results: int = Form(50),
    open_access_only: str | None = Form(None),
    download: str | None = Form(None),
    sort: str = Form("relevance"),
    source: str = Form(""),
):
    snap = tracker.snapshot()
    if snap.get("active"):
        _search_message["text"] = "A search or download is already running. Watch the live log on this page."
        _search_message["level"] = "warning"
        return RedirectResponse("/search?live=1", status_code=303)
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
    tracker.start_search(query.strip())
    _search_message["text"] = ""
    _search_message["level"] = "info"
    user_id = _request_user_id(request)

    async def _job():
        try:
            stats = await SearchService().run(filters, user_id=user_id)
            _search_message["text"] = (
                f"Finished “{query}”: {stats.unique_papers} unique papers, "
                f"{stats.pdfs_downloaded} PDFs downloaded."
            )
            _search_message["level"] = "success"
        except Exception as exc:
            _search_message["text"] = f"Search failed: {exc}"
            _search_message["level"] = "danger"

    background_tasks.add_task(_job)
    return RedirectResponse("/search?live=1", status_code=303)


@app.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    q: str = "",
    status: str = "",
    page: int = 1,
    latest: int = 0,
    pdf: int = 0,
    min_rating: int = 0,
    category: str = "",
    year: int = 0,
    source: str = "",
    journal: str = "",
    sort: str = DEFAULT_SORT,
    per_page: int = DEFAULT_PAGE_SIZE,
):
    downloadable = bool(pdf)
    min_rating = max(0, min(min_rating, 5))
    category = category.strip()
    source = source.strip()
    journal = journal.strip()
    sort = sort if sort in {key for key, _label in SORT_OPTIONS} else DEFAULT_SORT
    per_page = clamp_page_size(per_page)
    year = year if year and year > 0 else 0
    latest_search = None
    with session_scope() as session:
        latest_search = session.scalar(select(SearchQuery).order_by(SearchQuery.id.desc()).limit(1))
        use_latest = bool(latest) and latest_search is not None
        pager = pagination_spec(max(page, 1), 1, per_page)
        query_kwargs = dict(
            q=q,
            status=status,
            downloadable=downloadable,
            min_rating=min_rating,
            category=category,
            year=year or None,
            source=source,
            journal=journal,
            sort=sort,
            latest_search_id=latest_search.id if use_latest else None,
            limit=per_page,
        )
        papers, total = query_library(session, offset=0, **query_kwargs)
        pager = pagination_spec(max(page, 1), total, per_page)
        if pager["page"] > 1:
            papers, total = query_library(
                session, offset=(pager["page"] - 1) * per_page, **query_kwargs
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
        facets = library_facets(session)
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
        "min_rating": min_rating,
        "latest": use_latest,
        "category": category,
        "year": year,
        "source": source,
        "journal": journal,
        "sort": sort,
        "per_page": per_page,
    }
    has_filters = bool(q or status or downloadable or min_rating or category or year or source or journal)
    return templates.TemplateResponse(
        request,
        "library.html",
        _ctx(
            request,
            papers=papers,
            q=q,
            status=status,
            pdf=downloadable,
            min_rating=min_rating,
            category=category,
            year=year,
            source=source,
            journal=journal,
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
            search_running=bool(latest_search and latest_search.status == "running"),
        ),
    )


@app.post("/api/papers/{paper_id}/rating")
async def rate_paper(paper_id: int, request: Request):
    """Save or clear a 1–5 user rating for a paper."""
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
async def download_paper_pdf(request: Request, paper_id: int):
    try:
        path = await ensure_local_pdf(paper_id, topic_slug="library", user_id=_request_user_id(request))
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
async def download_oa(request: Request, background_tasks: BackgroundTasks, latest: str | None = Form(None)):
    search_id = None
    if latest:
        with session_scope() as session:
            row = session.scalar(select(SearchQuery).order_by(SearchQuery.id.desc()).limit(1))
            if row:
                search_id = row.id
    _search_message["text"] = "Downloading legally available PDFs. This can take a few minutes."
    _search_message["level"] = "info"
    user_id = _request_user_id(request)

    async def _job():
        try:
            stats = await download_open_access_papers(search_id=search_id, user_id=user_id)
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


@app.get("/api/download-progress")
def download_progress():
    return JSONResponse(tracker.snapshot(), headers={"Cache-Control": "no-store"})


@app.get("/api/search-progress")
def search_progress():
    return JSONResponse(tracker.snapshot(), headers={"Cache-Control": "no-store"})


@app.get("/downloads", response_class=HTMLResponse)
def downloads_page(
    request: Request,
    status: str = "",
    q: str = "",
    page: int = 1,
    per_page: int = DEFAULT_PAGE_SIZE,
):
    status = status.strip()
    q = q.strip()
    per_page = clamp_page_size(per_page)
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
        rows = list(
            session.scalars(stmt.offset((pager["page"] - 1) * per_page).limit(per_page)).all()
        )
        chip_stmt = (
            select(Download.status, func.count(Download.id))
            .join(Paper, Download.paper_id == Paper.id)
            .group_by(Download.status)
        )
        for clause in visible_download_clauses(status=""):
            chip_stmt = chip_stmt.where(clause)
        if q:
            chip_stmt = chip_stmt.where(or_(Paper.title.ilike(search_like), Paper.doi.ilike(search_like)))
        counts = dict(session.execute(chip_stmt).all())
    filters_state = {"status": status, "q": q, "per_page": per_page}
    has_filters = bool(status or q)
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
            per_page=per_page,
            pager=pager,
            filters=filters_state,
            page_sizes=PAGE_SIZES,
            has_filters=has_filters,
            progress=tracker.snapshot(),
        ),
    )


@app.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request):
    sources = _source_rows()
    return templates.TemplateResponse(request, "sources.html", _ctx(request, sources=sources, store=store_status().as_dict()))


def _source_rows() -> list[dict]:
    seed_academic_sources()
    searchable = {cls.name for cls in PROVIDER_CLASSES}
    sources = []
    for row in list_academic_sources():
        item = source_to_dict(row)
        item["searchable"] = row.slug in searchable
        sources.append(item)
    return sources


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
    return _ctx(
        request,
        config=cfg,
        root=str(ROOT_DIR),
        store=store_status().as_dict(),
        sources=sources,
        credentials=credentials,
        section=section,
        source_stats={"total": len(sources), "available": available, "disabled": sum(1 for s in sources if not s["enabled"])},
        timezones=timezone_choices(cfg.timezone),
        now_local=now_local(cfg.timezone).strftime("%Y-%m-%d %H:%M:%S"),
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, section: str = "workspace"):
    allowed = {"workspace", "search", "credentials", "sources"}
    if section not in allowed:
        section = "workspace"
    return templates.TemplateResponse(request, "settings.html", _settings_ctx(request, section))


def _safe_next(next_url: str | None, fallback: str) -> str:
    text = (next_url or "").strip()
    if text.startswith("/settings") or text.startswith("/sources"):
        return text
    return fallback


def _settings_redirect(section: str, message: str, level: str = "success", next_url: str | None = None) -> RedirectResponse:
    _search_message["text"] = message
    _search_message["level"] = level
    return RedirectResponse(_safe_next(next_url, f"/settings?section={section}"), status_code=303)


def _form_bool(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "on", "yes"}


@app.post("/settings/workspace")
def settings_save_workspace(
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
        return _settings_redirect("workspace", "Workspace settings saved to MySQL.")
    except SettingsError as exc:
        return _settings_redirect("workspace", str(exc), "danger")


@app.post("/settings/search")
def settings_save_search(
    download_limit: int = Form(100),
    default_max_results: int = Form(50),
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
                "max_concurrent_requests": max_concurrent_requests,
                "max_concurrent_downloads": max_concurrent_downloads,
                "request_timeout_seconds": request_timeout_seconds,
                "download_timeout_seconds": download_timeout_seconds,
                "max_redirects": max_redirects,
            }
        )
        return _settings_redirect("search", "Search and download settings saved to MySQL.")
    except SettingsError as exc:
        return _settings_redirect("search", str(exc), "danger")


@app.post("/settings/credentials")
async def settings_save_credentials(request: Request):
    form = await request.form()
    data = {str(k): str(v) for k, v in form.items()}
    try:
        save_credential_settings(data)
        return _settings_redirect("credentials", "API credentials saved to MySQL.")
    except SettingsError as exc:
        return _settings_redirect("credentials", str(exc), "danger")


@app.get("/api/sources/{source_id}")
def api_source_get(source_id: int):
    row = get_academic_source(source_id)
    if row is None:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    return {"ok": True, "source": source_to_dict(row)}


@app.post("/settings/sources")
def settings_create_source(
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
        return _settings_redirect("sources", f"Added academic source “{display_name}”.", next_url=next)
    except SettingsError as exc:
        return _settings_redirect("sources", str(exc), "danger", next_url=next)


@app.post("/settings/sources/{source_id}")
def settings_update_source(
    source_id: int,
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
        return _settings_redirect("sources", "Academic source updated.", next_url=next)
    except SettingsError as exc:
        return _settings_redirect("sources", str(exc), "danger", next_url=next)


@app.post("/settings/sources/{source_id}/delete")
def settings_delete_source(source_id: int, next: str = Form("")):
    try:
        delete_academic_source(source_id)
        return _settings_redirect("sources", "Academic source removed.", next_url=next)
    except SettingsError as exc:
        return _settings_redirect("sources", str(exc), "danger", next_url=next)


@app.post("/settings/sources/{source_id}/toggle")
def settings_toggle_source(source_id: int, request: Request, next: str = Form("")):
    try:
        row = toggle_academic_source(source_id)
        wants_json = "application/json" in (request.headers.get("accept") or "")
        if wants_json:
            return {"ok": True, "source": source_to_dict(row)}
        state = "enabled" if row.enabled else "disabled"
        return _settings_redirect("sources", f"{row.display_name} {state}.", next_url=next)
    except SettingsError as exc:
        if "application/json" in (request.headers.get("accept") or ""):
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return _settings_redirect("sources", str(exc), "danger", next_url=next)


def _named_counts(rows, total: int) -> list[dict]:
    return [{"name": name, "count": count, "pct": share(count, total)} for name, count in rows if name]


@app.get("/statistics", response_class=HTMLResponse)
def statistics_page(request: Request):
    source_names = {str(item["slug"]): str(item["display_name"]) for item in BUILTIN_SOURCES}
    visible = visible_paper_clauses()
    with session_scope() as session:
        stored_total = session.scalar(select(func.count(Paper.id))) or 0
        total = session.scalar(select(func.count(Paper.id)).where(*visible)) or 0
        oa = session.scalar(select(func.count(Paper.id)).where(Paper.open_access.is_(True), *visible)) or 0
        downloadable = session.scalar(select(func.count(Paper.id)).where(downloadable_clause(), *visible)) or 0
        downloaded = session.scalar(select(func.count(Download.id)).where(Download.status == "DOWNLOADED")) or 0
        paywalled = session.scalar(select(func.count(Paper.id)).where(Paper.status == "PAYWALLED")) or 0
        failed = session.scalar(select(func.count(Download.id)).where(Download.status == "FAILED")) or 0
        rated = session.scalar(
            select(func.count(Paper.id)).where(Paper.user_rating.is_not(None), *visible)
        ) or 0
        searches = session.scalar(select(func.count(SearchQuery.id))) or 0
        avg_citations = session.scalar(
            select(func.avg(Paper.citation_count)).where(Paper.citation_count.is_not(None), *visible)
        )
        avg_rating = session.scalar(
            select(func.avg(Paper.user_rating)).where(Paper.user_rating.is_not(None), *visible)
        )
        year_bounds = session.execute(
            select(func.min(Paper.publication_year), func.max(Paper.publication_year)).where(
                Paper.publication_year.is_not(None), *visible
            )
        ).one()
        years = session.execute(
            select(Paper.publication_year, func.count(Paper.id))
            .where(Paper.publication_year.is_not(None), *visible)
            .group_by(Paper.publication_year)
            .order_by(Paper.publication_year)
        ).all()
        publishers = session.execute(
            select(Paper.publisher, func.count(Paper.id))
            .where(Paper.publisher.is_not(None), Paper.publisher != "", *visible)
            .group_by(Paper.publisher)
            .order_by(func.count(Paper.id).desc())
            .limit(10)
        ).all()
        journals = session.execute(
            select(Paper.journal, func.count(Paper.id))
            .where(Paper.journal.is_not(None), Paper.journal != "", *visible)
            .group_by(Paper.journal)
            .order_by(func.count(Paper.id).desc())
            .limit(10)
        ).all()
        authors = session.execute(
            select(Author.name, func.count(PaperAuthor.id))
            .join(PaperAuthor, PaperAuthor.author_id == Author.id)
            .join(Paper, Paper.id == PaperAuthor.paper_id)
            .where(*visible)
            .group_by(Author.name)
            .order_by(func.count(PaperAuthor.id).desc())
            .limit(10)
        ).all()
        sources = session.execute(
            select(Paper.source, func.count(Paper.id))
            .where(Paper.source.is_not(None), Paper.source != "", *visible)
            .group_by(Paper.source)
            .order_by(func.count(Paper.id).desc())
            .limit(10)
        ).all()
        status_rows = session.execute(
            select(Paper.status, func.count(Paper.id)).where(*visible).group_by(Paper.status)
        ).all()
        rating_rows = session.execute(
            select(Paper.user_rating, func.count(Paper.id))
            .where(Paper.user_rating.is_not(None), *visible)
            .group_by(Paper.user_rating)
            .order_by(Paper.user_rating.desc())
        ).all()
        top_cited = session.scalars(
            select(Paper)
            .where(Paper.citation_count.is_not(None), *visible)
            .order_by(Paper.citation_count.desc())
            .limit(8)
        ).all()
        topics = session.execute(
            select(SearchQuery.original_query, func.count(SearchQuery.id))
            .group_by(SearchQuery.original_query)
            .order_by(func.count(SearchQuery.id).desc())
            .limit(6)
        ).all()

    year_counts = [
        {"year": year, "yy": f"{year % 100:02d}", "count": count, "pct": share(count, total)}
        for year, count in years
    ]
    year_max = max((row["count"] for row in year_counts), default=0)
    year_chart = year_counts[-16:]
    for row in year_chart:
        row["bar"] = round((row["count"] / year_max) * 100, 1) if year_max else 0
    peak = max(year_counts, key=lambda row: row["count"]) if year_counts else None
    statuses = [
        {
            "code": code,
            "count": count,
            "pct": share(count, total),
            **status_meta(code),
        }
        for code, count in status_rows
        if code
    ]
    statuses.sort(key=lambda row: (-row["count"], row["label"]))
    source_rows = [
        {
            "name": source_names.get(slug, (slug or "").replace("_", " ").title()),
            "slug": slug,
            "count": count,
            "pct": share(count, total),
        }
        for slug, count in sources
        if slug
    ]
    kpis = [
        {"href": "/library", "label": "Papers in library", "value": total, "tone": "primary", "hint": "All stored records" if get_runtime_config().show_paywalled else "Visible records"},
        {"href": "/library?pdf=1", "label": "Downloadable PDFs", "value": downloadable, "tone": "success", "hint": f"{share(downloadable, total)}% of library"},
        {"href": "/downloads?status=DOWNLOADED", "label": "Downloaded", "value": downloaded, "tone": "info", "hint": "Saved to disk"},
        {"href": "/library?status=PAYWALLED", "label": "Paywalled", "value": paywalled, "tone": "warning", "hint": f"{share(paywalled, stored_total)}% of library"},
        {"href": "/library?status=OA_AVAILABLE", "label": "Open access", "value": oa, "tone": "secondary", "hint": f"{share(oa, total)}% of library"},
        {"href": "/library?min_rating=1", "label": "Rated papers", "value": rated, "tone": "primary", "hint": f"Avg {avg_rating:.1f}" if avg_rating else "No ratings yet"},
    ]
    insights = []
    if year_bounds[0] and year_bounds[1]:
        insights.append(f"Coverage {year_bounds[0]}–{year_bounds[1]}")
    if peak:
        insights.append(f"Peak year {peak['year']} · {peak['count']} papers")
    if total:
        if get_runtime_config().show_paywalled:
            insights.append(f"{share(paywalled, stored_total)}% paywalled")
        insights.append(f"{share(downloadable, total)}% have a legal PDF")
    if avg_citations:
        insights.append(f"Avg citations {avg_citations:.0f}")
    if failed:
        insights.append(f"{failed} failed download{'s' if failed != 1 else ''}")
    return templates.TemplateResponse(
        request,
        "statistics.html",
        _ctx(
            request,
            total=total,
            stored_total=stored_total,
            failed=failed,
            avg_citations=avg_citations,
            year_span=year_bounds,
            years=year_counts,
            year_chart=year_chart,
            year_max=year_max,
            no_year=max(total - sum(row["count"] for row in year_counts), 0),
            publishers=_named_counts(publishers, total),
            journals=_named_counts(journals, total),
            authors=_named_counts(authors, total),
            sources=source_rows,
            statuses=statuses,
            ratings=[{"rating": rating, "count": count, "pct": share(count, rated)} for rating, count in rating_rows],
            top_cited=top_cited,
            topics=[{"name": name, "count": count} for name, count in topics],
            kpis=kpis,
            insights=insights,
        ),
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
