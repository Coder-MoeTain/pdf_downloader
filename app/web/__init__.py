"""Local FastAPI dashboard for Cyber Scholar."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __app_name__, __version__

# Re-exported for tests that patch app.web.* (handlers import these names locally too).
from app.auth import (
    auth_required as auth_required,
)
from app.auth import (
    current_user as current_user,
)
from app.auth import (
    google_login_enabled as google_login_enabled,
)
from app.auth import (
    setup_required,
)
from app.auth import (
    user_is_admin as user_is_admin,
)
from app.auth import (
    user_role as user_role,
)
from app.config import (
    allowed_hosts,
    app_env,
    https_assumed,
    session_secret_value,
    trusted_proxy_ips,
    validate_startup_config,
)
from app.database.connection import init_db
from app.database.settings_repository import apply_top20_source_limits, seed_academic_sources
from app.exceptions import CsrfError
from app.security.csrf import CsrfMiddleware, csrf_failure, csrf_protect
from app.security.headers import SecurityHeadersMiddleware
from app.services.crawl_queue import start_crawl_queue_worker
from app.services.crawl_schedule import start_crawl_schedule_worker
from app.services.download_queue import start_download_worker
from app.services.download_service import ensure_local_pdf as ensure_local_pdf
from app.services.search_queue import start_search_queue_worker
from app.services.usage import record_usage as record_usage
from app.utils.git_update import git_pull as git_pull
from app.utils.git_update import git_status as git_status
from app.utils.logger import setup_logging
from app.web.dependencies import WEB_DIR
from app.web.dependencies import _crawl_source_rows as _crawl_source_rows
from app.web.middleware import AuthGateMiddleware
from app.web.routes.admin import router as admin_router
from app.web.routes.auth import router as auth_router
from app.web.routes.cfp import router as cfp_router
from app.web.routes.downloads import router as downloads_router
from app.web.routes.library import router as library_router
from app.web.routes.search import router as search_router
from app.web.routes.settings import router as settings_router
from app.web.routes.system import router as system_router

app = FastAPI(title=__app_name__, version=__version__, dependencies=[Depends(csrf_protect)])
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


@app.exception_handler(CsrfError)
async def csrf_exception_handler(request: Request, _exc: CsrfError):
    return csrf_failure(request)


app.add_middleware(AuthGateMiddleware)
app.add_middleware(CsrfMiddleware)
_https_only = app_env() == "production"
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret_value(),
    session_cookie="cs_session",
    same_site="lax",
    https_only=_https_only,
    max_age=60 * 60 * 12,
)
app.add_middleware(SecurityHeadersMiddleware)
_proxy_ips = trusted_proxy_ips()
if _proxy_ips and "*" not in _proxy_ips:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_proxy_ips)
if app_env() == "production" and https_assumed():
    from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware

    app.add_middleware(HTTPSRedirectMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())

app.include_router(auth_router)
app.include_router(search_router)
app.include_router(library_router)
app.include_router(downloads_router)
app.include_router(cfp_router)
app.include_router(settings_router)
app.include_router(admin_router)
app.include_router(system_router)


@app.on_event("startup")
async def _startup() -> None:
    setup_logging()
    validate_startup_config()
    init_db()
    if setup_required():
        from app.security.bootstrap import ensure_bootstrap_token

        ensure_bootstrap_token()
    try:
        seed_academic_sources()
        apply_top20_source_limits()
    except Exception:
        pass
    await start_search_queue_worker()
    await start_crawl_queue_worker()
    await start_download_worker()
    await start_crawl_schedule_worker()

    try:
        from app.services.lms_watch import schedule_lms_sync, start_lms_watch

        start_lms_watch()
        schedule_lms_sync()
    except Exception:
        pass


@app.on_event("shutdown")
async def _shutdown() -> None:
    try:
        from app.services.download_queue import stop_download_worker

        await stop_download_worker()
    except Exception:
        pass
    try:
        from app.services.lms_watch import stop_lms_watch

        stop_lms_watch()
    except Exception:
        pass
