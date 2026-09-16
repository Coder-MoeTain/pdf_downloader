"""Shared helpers for research-workspace routes."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.database.connection import session_scope
from app.exceptions import AuthorizationError, CyberScholarError
from app.services.project_service import (
    ProjectNotFound,
    ProjectPermissionError,
    get_accessible_project,
    review_type_label,
)
from app.web.dependencies import _ctx, _request_user_id
from app.web.flash import set_flash


def require_user_id(request: Request) -> int | None:
    return _request_user_id(request)


def project_error_response(request: Request, exc: Exception, *, json_mode: bool = False):
    if isinstance(exc, ProjectNotFound):
        if json_mode:
            return JSONResponse({"ok": False, "error": exc.public_message}, status_code=404)
        set_flash(request, exc.public_message, "danger")
        return RedirectResponse("/projects", status_code=303)
    if isinstance(exc, (ProjectPermissionError, AuthorizationError)):
        message = getattr(exc, "public_message", "You do not have access to this project.")
        if json_mode:
            return JSONResponse({"ok": False, "error": message}, status_code=403)
        set_flash(request, message, "warning")
        return RedirectResponse("/projects", status_code=303)
    if isinstance(exc, CyberScholarError):
        if json_mode:
            return JSONResponse({"ok": False, "error": exc.public_message}, status_code=exc.status_code)
        set_flash(request, exc.public_message, "danger")
        return RedirectResponse("/projects", status_code=303)
    raise exc


def load_project(request: Request, project_id: int, *, min_role: str = "viewer"):
    user_id = _request_user_id(request)
    if user_id is None:
        raise ProjectPermissionError("Sign in required.")
    with session_scope() as session:
        project, member = get_accessible_project(session, project_id, user_id, min_role=min_role)
        session.expunge(project)
        session.expunge(member)
    return project, member, user_id


PROJECT_TABS = (
    ("overview", "Overview", ""),
    ("papers", "Papers", "/papers"),
    ("screening", "Screening", "/screening"),
    ("extraction", "Extraction", "/extraction"),
    ("matrix", "Matrix", "/matrix"),
    ("evidence", "Evidence", "/evidence"),
    ("prisma", "PRISMA", "/prisma"),
    ("analytics", "Analytics", "/analytics"),
    ("catalogs", "Catalogs", "/catalogs"),
    ("graph", "Graph", "/graph"),
    ("ask", "Ask Research", "/ask"),
    ("export", "Export", "/export"),
)


def project_ctx(request: Request, project, member, **extra):
    tabs = [
        {
            "key": key,
            "label": label,
            "href": f"/projects/{project.id}{suffix}",
        }
        for key, label, suffix in PROJECT_TABS
    ]
    payload = _ctx(
        request,
        project=project,
        member=member,
        project_role=member.role,
        project_tabs=tabs,
        breadcrumbs=[
            {"href": "/", "label": "Cyber Scholar"},
            {"href": "/projects", "label": "Projects"},
            {"href": f"/projects/{project.id}", "label": project.title},
        ],
        review_type_label=review_type_label(project.review_type),
        **extra,
    )
    payload["page"] = "projects"
    return payload
