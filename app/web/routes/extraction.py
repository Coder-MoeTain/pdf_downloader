"""Extraction, quality assessment, and evidence-linked field values."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.database.connection import session_scope
from app.database.models import Paper
from app.database.research_models import ExtractedValue, ExtractionField, ProjectPaper, ResearchProject
from app.services.extraction_service import (
    add_custom_field,
    project_schema,
    quality_checklist,
    save_quality_answers,
    set_extracted_value,
)
from app.web.dependencies import templates
from app.web.flash import set_flash
from app.web.research_deps import load_project, project_ctx, project_error_response

router = APIRouter()


@router.get("/projects/{project_id}/extraction", response_class=HTMLResponse)
def extraction_page(request: Request, project_id: int, item: int | None = None):
    try:
        project, member, user_id = load_project(request, project_id, min_role="reviewer")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        schema = project_schema(session, project.id)
        fields = []
        if schema:
            fields = list(
                session.scalars(
                    select(ExtractionField)
                    .where(ExtractionField.schema_id == schema.id)
                    .order_by(ExtractionField.position)
                ).all()
            )
        papers = list(
            session.execute(
                select(ProjectPaper, Paper)
                .join(Paper, Paper.id == ProjectPaper.paper_id)
                .where(ProjectPaper.project_id == project.id, ProjectPaper.is_duplicate.is_(False))
                .order_by(ProjectPaper.id)
            ).all()
        )
        current = session.get(ProjectPaper, item) if item else (papers[0][0] if papers else None)
        paper = session.get(Paper, current.paper_id) if current else None
        values = {}
        if current:
            values = {
                row.field_id: row
                for row in session.scalars(
                    select(ExtractedValue).where(ExtractedValue.project_paper_id == current.id)
                ).all()
            }
        checklist = quality_checklist(session, project.id)
        questions = list(checklist.questions) if checklist else []
    return templates.TemplateResponse(
        request,
        "projects/extraction.html",
        project_ctx(
            request,
            project,
            member,
            active_tab="extraction",
            fields=fields,
            papers=papers,
            current=current,
            paper=paper,
            values=values,
            questions=questions,
        ),
    )


@router.post("/projects/{project_id}/extraction/{project_paper_id}")
def extraction_save(
    request: Request,
    project_id: int,
    project_paper_id: int,
    field_id: int = Form(...),
    value: str = Form(""),
    evidence_text: str = Form(""),
    page_number: str = Form(""),
    section: str = Form(""),
):
    try:
        project, member, user_id = load_project(request, project_id, min_role="editor")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        set_extracted_value(
            session,
            live,
            user_id,
            project_paper_id,
            field_id,
            value,
            evidence_text=evidence_text,
            page_number=int(page_number) if page_number.strip().isdigit() else None,
            section=section,
            state="user_edited",
            method="manual",
        )
    set_flash(request, "Extraction saved. Unverified AI values are never stored as verified.", "success")
    return RedirectResponse(f"/projects/{project_id}/extraction?item={project_paper_id}", status_code=303)


@router.post("/projects/{project_id}/extraction/fields")
def extraction_add_field(request: Request, project_id: int, key: str = Form(""), label: str = Form(...)):
    try:
        project, member, user_id = load_project(request, project_id, min_role="owner")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        add_custom_field(session, project.id, key=key or label, label=label)
    return RedirectResponse(f"/projects/{project_id}/extraction", status_code=303)


@router.post("/projects/{project_id}/quality/{project_paper_id}")
async def quality_save(request: Request, project_id: int, project_paper_id: int, notes: str = Form("")):
    try:
        project, member, user_id = load_project(request, project_id, min_role="reviewer")
    except Exception as exc:
        return project_error_response(request, exc)
    form = await request.form()
    answers = {}
    for key, raw in form.items():
        if str(key).startswith("q_") and str(key)[2:].isdigit():
            answers[int(str(key)[2:])] = str(raw)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        save_quality_answers(session, live, user_id, project_paper_id, answers, notes=notes)
    set_flash(request, "Quality assessment saved.", "success")
    return RedirectResponse(f"/projects/{project_id}/extraction?item={project_paper_id}", status_code=303)
