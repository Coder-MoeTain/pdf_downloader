"""PRISMA diagram and count exports."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.database.connection import session_scope
from app.database.research_models import ResearchProject
from app.services.prisma_service import prisma_counts, prisma_pdf, prisma_png, prisma_svg
from app.web.dependencies import templates
from app.web.research_deps import load_project, project_ctx, project_error_response

router = APIRouter()


@router.get("/projects/{project_id}/prisma", response_class=HTMLResponse)
def prisma_page(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        counts = prisma_counts(session, live)
        svg = prisma_svg(counts, title=project.title)
    return templates.TemplateResponse(
        request,
        "projects/prisma.html",
        project_ctx(request, project, member, active_tab="prisma", counts=counts, svg=svg),
    )


@router.get("/projects/{project_id}/prisma.svg")
def prisma_svg_export(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        svg = prisma_svg(prisma_counts(session, live), title=project.title)
    return Response(svg, media_type="image/svg+xml")


@router.get("/projects/{project_id}/prisma.json")
def prisma_json_export(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        counts = prisma_counts(session, live)
    return Response(json.dumps(counts, indent=2), media_type="application/json")


@router.get("/projects/{project_id}/prisma.png")
def prisma_png_export(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        data = prisma_png(prisma_counts(session, live), title=project.title)
    return Response(data, media_type="image/png", headers={"Content-Disposition": "attachment; filename=prisma.png"})


@router.get("/projects/{project_id}/prisma.pdf")
def prisma_pdf_export(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        data = prisma_pdf(prisma_counts(session, live), title=project.title)
    return Response(
        data, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=prisma.pdf"}
    )
