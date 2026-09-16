"""Evidence synthesis, themes, and gap analysis."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.database.connection import session_scope
from app.database.research_models import ResearchProject, Theme
from app.services.evidence_service import add_evidence, add_theme, evidence_by_question
from app.services.gap_analysis import gap_clusters
from app.web.dependencies import templates
from app.web.flash import set_flash
from app.web.research_deps import load_project, project_ctx, project_error_response

router = APIRouter()


@router.get("/projects/{project_id}/evidence", response_class=HTMLResponse)
def evidence_page(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        groups = evidence_by_question(session, project.id)
        gaps = gap_clusters(session, live)
        themes = list(session.scalars(select(Theme).where(Theme.project_id == project.id)).all())
    return templates.TemplateResponse(
        request,
        "projects/evidence.html",
        project_ctx(request, project, member, active_tab="evidence", groups=groups, gaps=gaps, themes=themes),
    )


@router.post("/projects/{project_id}/evidence")
def evidence_create(
    request: Request,
    project_id: int,
    paper_id: int = Form(...),
    text: str = Form(...),
    stance: str = Form("supporting"),
    page_number: str = Form(""),
    section: str = Form(""),
    question_id: str = Form(""),
):
    try:
        project, member, user_id = load_project(request, project_id, min_role="editor")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        add_evidence(
            session,
            project.id,
            user_id,
            paper_id=paper_id,
            text=text,
            stance=stance,
            page_number=int(page_number) if page_number.strip().isdigit() else None,
            section=section,
            question_id=int(question_id) if question_id.strip().isdigit() else None,
        )
    set_flash(request, "Evidence recorded with paper and page provenance.", "success")
    return RedirectResponse(f"/projects/{project_id}/evidence", status_code=303)


@router.post("/projects/{project_id}/themes")
def evidence_theme(request: Request, project_id: int, name: str = Form(...)):
    try:
        project, member, user_id = load_project(request, project_id, min_role="editor")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        add_theme(session, project.id, name)
    return RedirectResponse(f"/projects/{project_id}/evidence", status_code=303)
