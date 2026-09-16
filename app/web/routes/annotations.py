"""Private PDF annotations and the paper reader workspace."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.database.connection import session_scope
from app.database.models import Paper
from app.database.repository import related_papers_with_reasons
from app.database.research_models import ProjectPaper
from app.services.annotation_service import add_annotation, delete_annotation, list_annotations
from app.web.dependencies import _ctx, _request_user_id, templates
from app.web.research_deps import load_project, project_ctx, project_error_response

router = APIRouter()


@router.get("/projects/{project_id}/papers/{paper_id}/reader", response_class=HTMLResponse)
def project_reader(request: Request, project_id: int, paper_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        link = session.scalar(select_project_paper(project.id, paper_id))
        paper = session.get(Paper, paper_id)
        notes = list_annotations(session, user_id, paper_id, project_id=project.id)
        related = related_papers_with_reasons(session, paper_id)
    if paper is None:
        return RedirectResponse(f"/projects/{project_id}/papers", status_code=303)
    pdf_url = f"/papers/{paper_id}/pdf?inline=1"
    return templates.TemplateResponse(
        request,
        "projects/reader.html",
        project_ctx(
            request,
            project,
            member,
            active_tab="papers",
            paper=paper,
            annotations=notes,
            related=related,
            pdf_url=pdf_url,
            has_link=link is not None,
        ),
    )


def select_project_paper(project_id: int, paper_id: int):
    from sqlalchemy import select

    return select(ProjectPaper).where(ProjectPaper.project_id == project_id, ProjectPaper.paper_id == paper_id)


@router.get("/library/papers/{paper_id}/reader", response_class=HTMLResponse)
def library_reader(request: Request, paper_id: int):
    user_id = _request_user_id(request)
    if user_id is None:
        return RedirectResponse(f"/login?next=/library/papers/{paper_id}/reader", status_code=302)
    with session_scope() as session:
        paper = session.get(Paper, paper_id)
        notes = list_annotations(session, user_id, paper_id)
        related = related_papers_with_reasons(session, paper_id)
    if paper is None:
        return RedirectResponse("/library", status_code=303)
    return templates.TemplateResponse(
        request,
        "projects/reader.html",
        _ctx(
            request,
            paper=paper,
            project=None,
            member=None,
            project_tabs=[],
            active_tab="papers",
            annotations=notes,
            related=related,
            pdf_url=f"/papers/{paper_id}/pdf?inline=1",
            breadcrumbs=[
                {"href": "/", "label": "Cyber Scholar"},
                {"href": "/library", "label": "Library"},
                {"href": f"/library/papers/{paper_id}/reader", "label": paper.title[:80]},
            ],
        ),
    )


@router.post("/api/papers/{paper_id}/annotations")
def create_annotation(
    request: Request,
    paper_id: int,
    project_id: int | None = Form(None),
    page_number: int = Form(1),
    annotation_type: str = Form("highlight"),
    selected_text: str = Form(""),
    comment: str = Form(""),
    color_key: str = Form("yellow"),
):
    user_id = _request_user_id(request)
    if user_id is None:
        return JSONResponse({"ok": False, "error": "Sign in required"}, status_code=401)
    with session_scope() as session:
        if session.get(Paper, paper_id) is None:
            return JSONResponse({"ok": False, "error": "Paper not found"}, status_code=404)
        row = add_annotation(
            session,
            user_id,
            paper_id,
            project_id=project_id,
            page_number=page_number,
            annotation_type=annotation_type,
            selected_text=selected_text,
            comment=comment,
            color_key=color_key,
        )
        annotation_id = row.id
    return JSONResponse({"ok": True, "id": annotation_id})


@router.post("/api/annotations/{annotation_id}/delete")
def remove_annotation(request: Request, annotation_id: int):
    user_id = _request_user_id(request)
    if user_id is None:
        return JSONResponse({"ok": False, "error": "Sign in required"}, status_code=401)
    with session_scope() as session:
        ok = delete_annotation(session, user_id, annotation_id)
    if not ok:
        return JSONResponse({"ok": False, "error": "Annotation not found"}, status_code=404)
    return JSONResponse({"ok": True})
