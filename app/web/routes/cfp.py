"""Call-for-Papers routes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.config import get_runtime_config
from app.database.connection import session_scope
from app.database.repository import (
    latest_cfp_fetch_at,
    list_upcoming_cfps,
)
from app.database.research_models import CfpBookmark
from app.services.cfp_service import refresh_status, schedule_cfp_refresh
from app.web.dependencies import (
    _ctx,
    _request_user_id,
    templates,
)
from app.web.flash import set_flash

router = APIRouter()


@router.get("/cfp", response_class=HTMLResponse)
async def cfp_page(
    request: Request,
    q: str = "",
    category: str = "",
    country: str = "",
    sort: str = "deadline",
    verified: str = "",
):
    """Render cached CFPs only — never wait on WikiCFP or a long SQLite lock."""
    q = q.strip()
    category = category.strip()
    country = country.strip()
    sort = sort if sort in {"deadline", "event", "recent"} else "deadline"
    verified_only = verified in {"1", "true", "on", "yes"}

    def _build() -> dict:
        rows: list = []
        fetched_at = None
        bookmarks: set[int] = set()
        cfg = get_runtime_config()
        list_limit = max(1, min(200, int(getattr(cfg, "cfp_list_limit", 30) or 30)))
        user_id = _request_user_id(request)
        try:
            with session_scope() as session:
                # Fail fast if another writer (background refresh) holds the DB.
                session.connection().exec_driver_sql("PRAGMA busy_timeout=1500")
                rows = list_upcoming_cfps(
                    session,
                    limit=list_limit,
                    q=q,
                    category=category,
                    country=country,
                    verified_only=verified_only,
                    sort=sort,
                )
                fetched_at = latest_cfp_fetch_at(session)
                if user_id and rows:
                    bookmarks = set(
                        session.scalars(
                            select(CfpBookmark.cfp_id).where(
                                CfpBookmark.user_id == user_id,
                                CfpBookmark.cfp_id.in_([row.id for row in rows]),
                            )
                        ).all()
                    )
        except OperationalError:
            rows = []
            fetched_at = None
        status = refresh_status()
        categories = sorted(
            {part.strip() for row in rows if row.categories for part in str(row.categories).split(",") if part.strip()}
        )
        countries = sorted({(row.location or "").strip() for row in rows if (row.location or "").strip()})
        return _ctx(
            request,
            calls=rows,
            fetched_at=fetched_at,
            window_days=90,
            list_limit=list_limit,
            refresh=status,
            cfp_auto_refresh=not rows and not status.get("running") and not (q or category or country or verified_only),
            q=q,
            category=category,
            country=country,
            sort=sort,
            verified_only=verified_only,
            bookmark_ids=bookmarks,
            cfp_categories=categories,
            cfp_countries=countries,
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


@router.post("/cfp/{cfp_id}/bookmark")
def cfp_bookmark(request: Request, cfp_id: int):
    user_id = _request_user_id(request)
    if user_id is None:
        return RedirectResponse("/login?next=/cfp", status_code=302)
    with session_scope() as session:
        existing = session.scalar(
            select(CfpBookmark).where(CfpBookmark.user_id == user_id, CfpBookmark.cfp_id == cfp_id)
        )
        if existing is None:
            session.add(CfpBookmark(user_id=user_id, cfp_id=cfp_id))
            set_flash(request, "Saved this call for papers.", "success")
        else:
            session.delete(existing)
            set_flash(request, "Removed saved call for papers.", "info")
    return RedirectResponse("/cfp", status_code=303)
