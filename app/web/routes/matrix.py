"""Research matrix view and exports."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from app.database.connection import session_scope
from app.database.research_models import ResearchProject
from app.services.extraction_service import latex_table, matrix_csv, matrix_json, matrix_rows, matrix_xlsx
from app.web.dependencies import templates
from app.web.research_deps import load_project, project_ctx, project_error_response

router = APIRouter()


@router.get("/projects/{project_id}/matrix", response_class=HTMLResponse)
def matrix_page(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        payload = matrix_rows(session, live)
    return templates.TemplateResponse(
        request,
        "projects/matrix.html",
        project_ctx(request, project, member, active_tab="matrix", matrix=payload),
    )


@router.get("/projects/{project_id}/matrix.csv")
def matrix_csv_export(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        payload = matrix_rows(session, live)
    return Response(
        matrix_csv(payload), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=matrix.csv"}
    )


@router.get("/projects/{project_id}/matrix.xlsx")
def matrix_xlsx_export(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        payload = matrix_rows(session, live)
    return Response(
        matrix_xlsx(payload),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=matrix.xlsx"},
    )


@router.get("/projects/{project_id}/matrix.json")
def matrix_json_export(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        payload = matrix_rows(session, live)
    return Response(matrix_json(payload), media_type="application/json")


@router.get("/projects/{project_id}/matrix.tex")
def matrix_tex_export(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        payload = matrix_rows(session, live)
    return PlainTextResponse(latex_table(payload), headers={"Content-Disposition": "attachment; filename=matrix.tex"})
