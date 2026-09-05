"""Local FastAPI dashboard for Cyber Scholar."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import selectinload

from app import __app_name__, __app_subtitle__, __version__
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
from app.database.connection import init_db, retry_on_sqlite_lock, session_scope
from app.database.models import Author, Download, Paper, PaperAuthor, SearchQuery, SearchResult, User
from app.database.repository import (
    active_crawl_job_for_user,
    active_search_job_for_user,
    delete_library_paper,
    download_user_options,
    downloadable_clause,
    get_crawl_job,
    get_search_job,
    library_facets,
    query_library,
    set_paper_rating,
    split_tags,
    visible_download_clauses,
    visible_paper_clauses,
)
from app.database.settings_repository import (
    SettingsError,
    apply_top20_source_limits,
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
    ensure_local_pdf,
    existing_pdf_path,
    pdf_button_state,
    safe_library_pdf,
)
from app.services.download_queue import enqueue_oa_download, oa_download_active, start_download_worker
from app.services.crawl_queue import (
    cancel_crawl,
    crawl_progress_snapshot,
    enqueue_crawl,
    queue_snapshot as crawl_queue_snapshot,
    start_crawl_queue_worker,
)
from app.services.crawl_service import filters_from_form
from app.services.progress import crawl_idle_snapshot, crawl_job_registry, download_tracker, job_registry, live_progress, tracker
from app.services.search_queue import (
    cancel_search,
    enqueue_search,
    queue_snapshot,
    search_progress_snapshot,
    start_search_queue_worker,
)
from app.services.search_service import SearchService, filters_from_cli
from app.services.library_reset import reset_library_repository
from app.services.usage import activity_payload, drop_presence, record_usage, touch_presence
from app.utils.git_update import GitUpdateError, git_pull, git_status
from app.utils.pm2_control import Pm2Error, pm2_logs, pm2_restart, pm2_status
from app.utils.logger import setup_logging
from app.utils.time import format_local, now_local, timezone_abbrev, timezone_choices, timezone_offset_label
from app.web.ui import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SORT,
    PAGE_SIZES,
    SORT_OPTIONS,
    QueryInt,
    QueryPage,
    active_page,
    clamp_page_size,
    download_actor_name,
    downloads_href,
    is_new_download,
    library_href,
    library_status_panel,
    ordered_source_status_counts,
    ordered_status_counts,
    pagination_spec,
    source_matches,
    paper_abstract_meta,
    paper_authors_line,
    paper_categories,
    paper_downloader_name,
    paper_record_date,
    download_record_date,
    share,
    source_label,
    source_logo_url,
    source_homepage,
    source_row_status,
    sources_href,
    status_meta,
)

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
templates.env.globals["pdf_button_state"] = pdf_button_state
templates.env.globals["status_meta"] = status_meta
templates.env.globals["can_preview"] = lambda paper: existing_pdf_path(paper) is not None
templates.env.globals["library_href"] = library_href
templates.env.globals["downloads_href"] = downloads_href
templates.env.globals["sources_href"] = sources_href
templates.env.globals["source_label"] = source_label
templates.env.globals["source_logo_url"] = source_logo_url
templates.env.globals["source_homepage"] = source_homepage
templates.env.globals["paper_abstract_meta"] = paper_abstract_meta
templates.env.globals["paper_authors_line"] = paper_authors_line
templates.env.globals["paper_categories"] = paper_categories
templates.env.globals["paper_downloader_name"] = paper_downloader_name
templates.env.globals["paper_record_date"] = paper_record_date
templates.env.globals["download_actor_name"] = download_actor_name
templates.env.globals["download_record_date"] = download_record_date
templates.env.globals["is_new_download"] = is_new_download
templates.env.filters["filesize"] = lambda value: _format_bytes(value)
templates.env.filters["localdt"] = lambda value, fmt="%Y-%m-%d %H:%M": format_local(value, fmt)
templates.env.filters["tags"] = split_tags

app = FastAPI(title=__app_name__, version=__version__)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

_search_message = {"text": "", "level": "info"}
_git_log = {"text": ""}
_pm2_log = {"text": ""}


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
    if user:
        touch_presence(user, path=path)
    return await call_next(request)


app.add_middleware(SessionMiddleware, secret_key=session_secret(), same_site="lax", https_only=False)


@app.on_event("startup")
async def _startup() -> None:
    setup_logging()
    init_db()
    try:
        seed_academic_sources()
        apply_top20_source_limits()
    except Exception:
        pass
    await start_search_queue_worker()
    await start_crawl_queue_worker()
    await start_download_worker()
    try:
        from app.services.lms_watch import schedule_lms_sync, start_lms_watch

        start_lms_watch()
        schedule_lms_sync()
    except Exception:
        pass


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
        def _persist_local():
            with session_scope() as session:
                if user_count() == 0:
                    row = create_local_user(session, email=email, password=password, name=name, role=ROLE_ADMIN)
                else:
                    row = authenticate_local(session, email, password)
                if row is None:
                    return None
                return user_to_session(row)

        payload = retry_on_sqlite_lock(_persist_local)
        if payload is None:
            _search_message["text"] = "Email or password is not correct."
            _search_message["level"] = "danger"
            return templates.TemplateResponse(request, "login.html", _login_ctx(request, nxt), status_code=401)
        request.session["user"] = payload
        signed_email = payload["email"]
        is_admin = user_role(payload) == ROLE_ADMIN
        touch_presence(payload, path="/")
        record_usage(request, "login", f"Signed in as {signed_email}")
    except ValueError as exc:
        _search_message["text"] = str(exc)
        _search_message["level"] = "danger"
        return templates.TemplateResponse(request, "login.html", _login_ctx(request, nxt), status_code=400)
    except OperationalError:
        _search_message["text"] = "The library database was busy. Wait a moment and try again."
        _search_message["level"] = "warning"
        return templates.TemplateResponse(request, "login.html", _login_ctx(request, nxt), status_code=503)
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
    try:
        def _persist_google():
            with session_scope() as session:
                row = upsert_google_user(
                    session,
                    google_id=google_id,
                    email=email,
                    name=str(info.get("name") or ""),
                    picture=str(info.get("picture") or "") or None,
                )
                return user_to_session(row), bool(row.is_admin)

        payload, is_admin = retry_on_sqlite_lock(_persist_google)
        request.session["user"] = payload
        touch_presence(payload, path="/")
        record_usage(request, "login", f"Signed in with Google as {email}")
    except OperationalError:
        _search_message["text"] = "The library database was busy. Wait a moment and sign in again."
        _search_message["level"] = "warning"
        return RedirectResponse("/login", status_code=302)
    if is_admin_path(nxt) and not is_admin:
        nxt = "/"
    _search_message["text"] = f"Signed in as {email}."
    _search_message["level"] = "success"
    return RedirectResponse(nxt, status_code=302)


@app.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request):
    user = current_user(request)
    if user:
        record_usage(request, "logout", f"Signed out {user.get('email') or user.get('name') or ''}".strip())
        drop_presence(user.get("id"))
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
        "app_subtitle": cfg.subtitle or __app_subtitle__,
        "version": __version__,
        "flash": _search_message,
        "page": active_page(request.url.path),
        "progress": live_progress(),
        "download_progress": download_tracker.snapshot(),
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
        authors = session.execute(
            select(Author.name, func.count(PaperAuthor.id))
            .join(PaperAuthor, PaperAuthor.author_id == Author.id)
            .join(Paper, Paper.id == PaperAuthor.paper_id)
            .where(*visible)
            .group_by(Author.name)
            .order_by(func.count(PaperAuthor.id).desc())
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
            authors=[{"name": name, "count": count, "pct": share(count, total)} for name, count in authors],
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
    user_id = _request_user_id(request)
    is_admin = user_is_admin(request)
    job_id_param = request.query_params.get("job")
    with session_scope() as session:
        recent = session.scalars(select(SearchQuery).order_by(SearchQuery.created_at.desc()).limit(8)).all()
        active_job = active_search_job_for_user(session, user_id)
        focus_job = active_job
        if focus_job is None and job_id_param:
            try:
                jid = int(job_id_param)
                row = get_search_job(session, jid)
                if row and row.status in ("pending", "running"):
                    if is_admin or row.user_id == user_id:
                        focus_job = row
            except ValueError:
                pass
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
            topics=cfg.topics[:6],
        ),
    )


@app.post("/search")
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
    _search_message["text"] = f"Search queued (job #{job_id}). Sources and PDF downloads run in parallel — watch the live log."
    _search_message["level"] = "info"
    return RedirectResponse(f"/search?live=1&job={job_id}", status_code=303)


@app.get("/crawler", response_class=HTMLResponse)
def crawler_page(request: Request):
    if not user_is_admin(request):
        return RedirectResponse("/", status_code=302)
    user_id = _request_user_id(request)
    crawl_sources = _crawl_source_rows()
    crawlable_count = sum(1 for row in crawl_sources if row["crawlable"])
    job_id_param = request.query_params.get("job")
    with session_scope() as session:
        active_job = active_crawl_job_for_user(session, user_id)
        focus_job = active_job
        if focus_job is None and job_id_param:
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


@app.post("/crawler")
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
        _search_message["text"] = "Select at least one source to crawl."
        _search_message["level"] = "warning"
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
        _search_message["text"] = "No crawlable sources selected. Enable sources and pick ones that support paginated browse."
        _search_message["level"] = "warning"
        return RedirectResponse("/crawler", status_code=303)

    record_usage(request, "crawl", ", ".join(queued))
    if len(job_ids) == 1:
        msg = f"Crawl queued (job #{job_ids[0]}) for {queued[0]}."
    else:
        msg = f"Queued {len(job_ids)} crawls: {', '.join(queued)}."
    if skipped:
        msg += f" Skipped {len(skipped)} unavailable or search-only source(s)."
    _search_message["text"] = msg
    _search_message["level"] = "info"
    return RedirectResponse(f"/crawler?live=1&job={job_ids[0]}", status_code=303)


@app.get("/api/crawl-progress")
def crawl_progress(request: Request, job_id: int | None = None):
    if not user_is_admin(request):
        return JSONResponse({"ok": False, "error": "Admin access required"}, status_code=403)
    user_id = _request_user_id(request)
    target_id = job_id
    if target_id is None and user_id is not None:
        with session_scope() as session:
            active = active_crawl_job_for_user(session, user_id)
            if active:
                target_id = active.id
    if target_id is not None:
        snap = crawl_progress_snapshot(target_id)
        if snap:
            return JSONResponse(snap, headers={"Cache-Control": "no-store"})
    return JSONResponse(crawl_idle_snapshot(), headers={"Cache-Control": "no-store"})


@app.get("/api/crawl-queue")
def crawl_queue_api(request: Request):
    if not user_is_admin(request):
        return JSONResponse({"ok": False, "error": "Admin access required"}, status_code=403)
    user_id = _request_user_id(request)
    return JSONResponse(
        crawl_queue_snapshot(user_id=user_id, is_admin=True),
        headers={"Cache-Control": "no-store"},
    )


@app.post("/crawler/jobs/{job_id}/stop")
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
        _search_message["text"] = str(exc)
        _search_message["level"] = "warning"
        return RedirectResponse("/crawler", status_code=303)
    except ValueError as exc:
        if wants_json:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        _search_message["text"] = str(exc)
        _search_message["level"] = "warning"
        return RedirectResponse("/crawler", status_code=303)
    message = "Crawl stopped." if was == "pending" else "Stopping crawl…"
    _search_message["text"] = message
    _search_message["level"] = "info"
    if wants_json:
        return JSONResponse({"ok": True, "was": was, "message": message})
    return RedirectResponse(f"/crawler?live=1&job={job_id}", status_code=303)


@app.post("/search/jobs/{job_id}/stop")
async def search_stop(request: Request, job_id: int):
    user_id = _request_user_id(request)
    accept = (request.headers.get("accept") or "").lower()
    wants_json = "application/json" in accept and "text/html" not in accept
    try:
        was = cancel_search(job_id, user_id=user_id, is_admin=user_is_admin(request))
    except PermissionError as exc:
        if wants_json:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=403)
        _search_message["text"] = str(exc)
        _search_message["level"] = "warning"
        return RedirectResponse("/search", status_code=303)
    except ValueError as exc:
        if wants_json:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        _search_message["text"] = str(exc)
        _search_message["level"] = "warning"
        return RedirectResponse("/search", status_code=303)
    message = "Search stopped." if was == "pending" else "Stopping search…"
    _search_message["text"] = message
    _search_message["level"] = "info"
    if wants_json:
        return JSONResponse({"ok": True, "was": was, "message": message})
    return RedirectResponse("/search?live=1", status_code=303)


@app.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    q: str = "",
    status: str = "",
    page: QueryPage = 1,
    latest: QueryInt = 0,
    pdf: QueryInt = 0,
    min_rating: QueryInt = 0,
    category: str = "",
    year: QueryInt = 0,
    source: str = "",
    journal: str = "",
    user: QueryInt = 0,
    sort: str = DEFAULT_SORT,
    per_page: QueryInt = DEFAULT_PAGE_SIZE,
):
    downloadable = bool(pdf)
    min_rating = max(0, min(min_rating, 5))
    category = category.strip()
    source = source.strip()
    journal = journal.strip()
    sort = sort if sort in {key for key, _label in SORT_OPTIONS} else DEFAULT_SORT
    per_page = clamp_page_size(per_page)
    year = year if year and year > 0 else 0
    user_id = user if user and user > 0 else 0
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
            user_id=user_id or None,
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
        user_options = download_user_options(session, include_id=user_id or None)
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
        "user": user_id,
        "sort": sort,
        "per_page": per_page,
    }
    user_label = next((item["name"] for item in user_options if item["id"] == user_id), "")
    has_filters = bool(
        q or status or downloadable or min_rating or category or year or source or journal or user_id
    )
    stats = library_status_panel(facets)
    kpis = [
        {
            "href": library_href({"latest": use_latest, "sort": sort, "per_page": per_page}),
            "label": "Papers",
            "value": stats["visible_total"],
            "tone": "primary",
            "hint": "Visible in library",
            "active": not has_filters,
        },
        {
            "href": library_href(filters, pdf=True, page=1),
            "label": "Downloadable PDFs",
            "value": stats["downloadable"],
            "tone": "success",
            "hint": f"{share(stats['downloadable'], stats['visible_total'])}% of library",
            "active": bool(downloadable),
        },
        {
            "href": "/downloads?status=DOWNLOADED",
            "label": "Downloaded",
            "value": stats["downloaded"],
            "tone": "info",
            "hint": "Saved locally",
            "active": False,
        },
        {
            "href": library_href({"latest": use_latest, "sort": sort, "per_page": per_page}, pdf=True, page=1),
            "label": "Open access",
            "value": stats["open_access"],
            "tone": "secondary",
            "hint": "Legal PDF available",
            "active": False,
        },
        {
            "href": "/library",
            "label": "Paywalled",
            "value": stats["paywalled"],
            "tone": "warning",
            "hint": "Metadata only",
            "active": False,
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
            min_rating=min_rating,
            category=category,
            year=year,
            source=source,
            journal=journal,
            user=user_id,
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
            search_running=bool(use_latest and latest_search and latest_search.status == "running"),
            kpis=kpis,
            has_library_stats=stats["has_stats"],
            source_catalog={row["slug"]: row for row in _source_rows()},
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


@app.post("/papers/{paper_id}/delete")
def library_delete_paper(request: Request, paper_id: int, next: str = Form("")):
    if not user_is_admin(request):
        _search_message["text"] = "Only admins can delete papers from the library."
        _search_message["level"] = "warning"
        return RedirectResponse(_safe_next(next, "/library"), status_code=303)
    try:
        with session_scope() as session:
            title, paths = delete_library_paper(session, paper_id)
        for path_value in paths:
            dest = safe_library_pdf(path_value)
            if dest and dest.is_file():
                dest.unlink()
        _search_message["text"] = f"Deleted “{title}” from the library."
        _search_message["level"] = "success"
    except ValueError as exc:
        _search_message["text"] = str(exc)
        _search_message["level"] = "danger"
    return RedirectResponse(_safe_next(next, "/library"), status_code=303)


@app.get("/papers/{paper_id}/pdf")
async def download_paper_pdf(request: Request, paper_id: int):
    try:
        path = await ensure_local_pdf(paper_id, topic_slug="library", user_id=_request_user_id(request))
        record_usage(request, "download", path.name)
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
        _search_message["text"] = "A PDF download is already running. Watch progress on the Downloads page."
        _search_message["level"] = "info"
    elif enqueue_oa_download(search_id=search_id, user_id=user_id):
        _search_message["text"] = (
            "Downloading legally available PDFs. This can take a few minutes — "
            "you can browse other pages while downloads continue in the background."
        )
        _search_message["level"] = "info"
    else:
        _search_message["text"] = "Could not start the download queue. Try again in a moment."
        _search_message["level"] = "warning"
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
    return JSONResponse(download_tracker.snapshot(), headers={"Cache-Control": "no-store"})


@app.get("/api/search-progress")
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


@app.get("/api/activity")
def activity_api(request: Request):
    return JSONResponse(activity_payload(), headers={"Cache-Control": "no-store"})


@app.get("/api/search-queue")
def search_queue_api(request: Request):
    user_id = _request_user_id(request)
    is_admin = user_is_admin(request)
    return JSONResponse(
        queue_snapshot(user_id=user_id, is_admin=is_admin),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/downloads", response_class=HTMLResponse)
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
            user=user_id,
            user_label=user_label,
            user_options=user_options,
            per_page=per_page,
            pager=pager,
            filters=filters_state,
            page_sizes=PAGE_SIZES,
            has_filters=has_filters,
        ),
    )


@app.get("/sources", response_class=HTMLResponse)
def sources_page(
    request: Request,
    status: str = "",
    q: str = "",
    page: QueryPage = 1,
    per_page: QueryInt = DEFAULT_PAGE_SIZE,
):
    status = status.strip()
    q = q.strip()
    per_page = clamp_page_size(per_page)
    all_sources = _source_rows()
    searched = [item for item in all_sources if source_matches(item, q=q)]
    counts: dict[str, int] = {}
    for item in searched:
        code = source_row_status(item)
        counts[code] = counts.get(code, 0) + 1
    rows = [item for item in searched if source_matches(item, status=status)]
    pager = pagination_spec(max(page, 1), len(rows), per_page)
    start = (pager["page"] - 1) * per_page
    page_rows = rows[start : start + per_page]
    filters_state = {"status": status, "q": q, "per_page": per_page}
    return templates.TemplateResponse(
        request,
        "sources.html",
        _ctx(
            request,
            sources=page_rows,
            store=store_status().as_dict(),
            counts=counts,
            status_chips=ordered_source_status_counts(counts),
            status=status,
            q=q,
            per_page=per_page,
            pager=pager,
            filters=filters_state,
            page_sizes=PAGE_SIZES,
            has_filters=bool(status or q),
            source_stats={
                "total": len(all_sources),
                "available": sum(1 for item in all_sources if item["available"]),
            },
        ),
    )


def _source_rows() -> list[dict]:
    seed_academic_sources()
    searchable = {cls.name for cls in PROVIDER_CLASSES}
    sources = []
    for row in list_academic_sources():
        item = source_to_dict(row)
        item["searchable"] = row.slug in searchable
        sources.append(item)
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
        git=git_status() if section == "updates" else {"ok": False, "dirty": True, "error": ""},
        git_log=_git_log["text"] if section == "updates" else "",
        pm2=pm2_status() if section == "updates" else {"ok": False, "error": ""},
        pm2_log=_pm2_log["text"] if section == "updates" else "",
        activity=activity_payload() if section == "activity" else {"online": [], "online_count": 0, "events": [], "window_minutes": 5},
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, section: str = "workspace"):
    allowed = {"workspace", "search", "credentials", "sources", "updates", "activity"}
    if section not in allowed:
        section = "workspace"
    return templates.TemplateResponse(request, "settings.html", _settings_ctx(request, section))


def _safe_next(next_url: str | None, fallback: str) -> str:
    text = (next_url or "").strip()
    if text.startswith(("/settings", "/sources", "/library", "/search")):
        return text
    return fallback


def _settings_redirect(section: str, message: str, level: str = "success", next_url: str | None = None) -> RedirectResponse:
    _search_message["text"] = message
    _search_message["level"] = level
    return RedirectResponse(_safe_next(next_url, f"/settings?section={section}"), status_code=303)


def _form_bool(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "on", "yes"}


@app.post("/settings/update")
def settings_git_pull():
    try:
        result = git_pull()
        _git_log["text"] = result.get("output") or ""
        if result.get("already_current"):
            return _settings_redirect("updates", "Already up to date.")
        return _settings_redirect("updates", "Pulled the latest code. Restart PM2 if Python files changed.")
    except GitUpdateError as exc:
        _git_log["text"] = str(exc)
        return _settings_redirect("updates", str(exc), "danger")
    except Exception as exc:
        _git_log["text"] = str(exc)
        return _settings_redirect("updates", "Git pull failed.", "danger")


@app.post("/settings/pm2/restart")
def settings_pm2_restart():
    try:
        result = pm2_restart()
        _pm2_log["text"] = result.get("output") or ""
        name = (result.get("status") or {}).get("name") or "researchpaper"
        return _settings_redirect("updates", f"PM2 restarted {name}.")
    except Pm2Error as exc:
        _pm2_log["text"] = str(exc)
        return _settings_redirect("updates", str(exc), "danger")
    except Exception as exc:
        _pm2_log["text"] = str(exc)
        return _settings_redirect("updates", "PM2 restart failed.", "danger")


@app.post("/settings/pm2/logs")
def settings_pm2_logs():
    try:
        result = pm2_logs()
        _pm2_log["text"] = result.get("output") or ""
        return _settings_redirect("updates", f"Loaded PM2 logs for {result.get('name', 'researchpaper')}.")
    except Pm2Error as exc:
        _pm2_log["text"] = str(exc)
        return _settings_redirect("updates", str(exc), "danger")
    except Exception as exc:
        _pm2_log["text"] = str(exc)
        return _settings_redirect("updates", "Could not load PM2 logs.", "danger")


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


@app.post("/settings/reset")
def settings_reset_repository(request: Request, confirm: str = Form("")):
    if not user_is_admin(request):
        return RedirectResponse("/", status_code=302)
    if confirm.strip().upper() != "RESET":
        return _settings_redirect(
            "workspace",
            "Reset cancelled — type RESET in the confirmation box.",
            "warning",
        )
    try:
        stats = reset_library_repository()
    except Exception as exc:
        return _settings_redirect("workspace", f"Reset failed: {exc}", "danger")
    record_usage(
        request,
        "reset",
        f"{stats.papers} papers, {stats.search_jobs} search jobs, {stats.pdf_files_removed} PDFs",
    )
    return _settings_redirect(
        "workspace",
        (
            f"Library reset complete — removed {stats.papers} papers, "
            f"{stats.search_queries} searches, {stats.search_jobs} search jobs, "
            f"{stats.crawl_jobs} crawl jobs, and {stats.pdf_files_removed} PDF file(s)."
        ),
    )


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
