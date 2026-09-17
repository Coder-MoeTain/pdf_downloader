"""Top GitHub projects by research category."""

from __future__ import annotations

import asyncio
import json
from collections import Counter

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.exc import OperationalError

from app.database.connection import session_scope
from app.database.repository import latest_github_fetch_at, list_github_repos
from app.services.github_service import (
    GITHUB_CATEGORIES,
    category_label,
    github_category_map,
    merge_project_repos,
    refresh_status,
    repo_detail_payload,
    schedule_github_refresh,
    select_project_repos,
)
from app.web.dependencies import _ctx, templates
from app.web.flash import set_flash

router = APIRouter()


def _list_item(row, *, rank: int, categories: tuple[str, ...]) -> dict:
    payload = repo_detail_payload(row, rank=rank)
    seen: set[str] = set()
    cats = []
    for slug in categories:
        key = str(slug or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        cats.append({"slug": key, "label": category_label(key)})
    payload["categories"] = cats
    payload["search"] = " ".join(
        part
        for part in (
            payload.get("name"),
            payload.get("owner"),
            payload.get("full_name"),
            payload.get("description"),
            payload.get("language"),
            *(item["label"] for item in cats),
        )
        if part
    ).lower()
    return payload


@router.get("/projects", response_class=HTMLResponse)
async def projects_page(request: Request, category: str = ""):
    selected = (category or "").strip().lower()
    known = github_category_map()
    if selected and selected not in known:
        selected = ""

    def _build() -> dict:
        fetched_at = None
        try:
            with session_scope() as session:
                session.connection().exec_driver_sql("PRAGMA busy_timeout=1500")
                all_rows = list_github_repos(session)
                fetched_at = latest_github_fetch_at(session)
        except OperationalError:
            all_rows = []
            fetched_at = None
        raw_counts = Counter(row.category for row in all_rows)
        project_rows = select_project_repos(all_rows, limit=None)
        counts = Counter(row.category for row in project_rows)
        if selected:
            ranked_rows = [(row, (selected,)) for row in project_rows if row.category == selected]
        else:
            ranked_rows = merge_project_repos(project_rows)
        repos = [
            _list_item(row, rank=index, categories=categories)
            for index, (row, categories) in enumerate(ranked_rows, start=1)
        ]
        details = {item["key"]: item for item in repos}
        status = refresh_status()
        selected_meta = known.get(selected) or {}
        return _ctx(
            request,
            repos=repos,
            categories=GITHUB_CATEGORIES,
            category_counts=counts,
            raw_counts=raw_counts,
            selected=selected,
            selected_label=selected_meta.get("label") or "",
            fetched_at=fetched_at,
            refresh=status,
            project_page_json=json.dumps(
                {
                    "empty": not all_rows,
                    "running": bool(status.get("running")),
                    "autoStart": not all_rows and fetched_at is None and not status.get("running"),
                    "details": details,
                },
                ensure_ascii=True,
            ).replace("<", "\\u003c"),
            projects_auto_refresh=not all_rows and fetched_at is None and not status.get("running"),
        )

    ctx = await asyncio.to_thread(_build)
    return templates.TemplateResponse(request, "projects.html", ctx)


@router.get("/api/projects-status")
def projects_status_api():
    return JSONResponse(refresh_status(), headers={"Cache-Control": "no-store"})


@router.post("/api/projects-refresh")
async def projects_refresh_api():
    started = await asyncio.to_thread(schedule_github_refresh)
    status = refresh_status()
    return JSONResponse(
        {"ok": True, "started": started, **status},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/projects/refresh")
async def projects_refresh(request: Request, category: str = Form("")):
    started = await asyncio.to_thread(schedule_github_refresh)
    if started:
        set_flash(
            request,
            "Refreshing top GitHub projects in the background. This page will update when ready.",
            "info",
        )
    else:
        status = refresh_status()
        if status.get("running"):
            set_flash(request, "A GitHub refresh is already running. Stay on this page.", "info")
        else:
            set_flash(request, status.get("message") or "Could not start refresh.", "info")
    selected = (category or "").strip().lower()
    suffix = f"?category={selected}" if selected else ""
    return RedirectResponse(f"/projects{suffix}", status_code=303)
