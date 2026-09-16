"""Call-for-Papers routes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.exc import OperationalError

from app.config import get_runtime_config
from app.database.connection import session_scope
from app.database.repository import (
    latest_cfp_fetch_at,
    list_upcoming_cfps,
)
from app.services.cfp_service import refresh_status, schedule_cfp_refresh
from app.web.dependencies import (
    _ctx,
    templates,
)
from app.web.flash import set_flash

router = APIRouter()


@router.get("/cfp", response_class=HTMLResponse)
async def cfp_page(request: Request):
    """Render cached CFPs only — never wait on WikiCFP or a long SQLite lock."""

    def _build() -> dict:
        rows: list = []
        fetched_at = None
        cfg = get_runtime_config()
        list_limit = max(1, min(200, int(getattr(cfg, "cfp_list_limit", 30) or 30)))
        try:
            with session_scope() as session:
                # Fail fast if another writer (background refresh) holds the DB.
                session.connection().exec_driver_sql("PRAGMA busy_timeout=1500")
                rows = list_upcoming_cfps(session, limit=list_limit)
                fetched_at = latest_cfp_fetch_at(session)
        except OperationalError:
            rows = []
            fetched_at = None
        status = refresh_status()
        return _ctx(
            request,
            calls=rows,
            fetched_at=fetched_at,
            window_days=90,
            list_limit=list_limit,
            refresh=status,
            cfp_auto_refresh=not rows and not status.get("running"),
        )

    ctx = await asyncio.to_thread(_build)
    return templates.TemplateResponse(request, "cfp.html", ctx)


@router.get("/api/cfp-status")
def cfp_status_api():
    return JSONResponse(refresh_status(), headers={"Cache-Control": "no-store"})


@router.post("/api/cfp-refresh")
async def cfp_refresh_api():
    started = await asyncio.to_thread(lambda: schedule_cfp_refresh(force=True))
    status = refresh_status()
    return JSONResponse(
        {"ok": True, "started": started, **status},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/cfp/refresh")
async def cfp_refresh(request: Request):
    started = await asyncio.to_thread(lambda: schedule_cfp_refresh(force=True))
    if started:
        set_flash(
            request,
            "Refreshing Call for Papers from WikiCFP in the background. This page will update when ready.",
            "info",
        )
    else:
        status = refresh_status()
        if status.get("running"):
            set_flash(
                request,
                "A Call for Papers refresh is already running. Stay on this page — it will reload when finished.",
                "info",
            )
        else:
            set_flash(request, status.get("message") or "Could not start refresh.", "info")
    return RedirectResponse("/cfp", status_code=303)
