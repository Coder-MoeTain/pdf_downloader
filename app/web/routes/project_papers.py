"""Project paper helpers and bibliography export."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import select

from app.database.connection import session_scope
from app.database.models import Paper
from app.database.research_models import ProjectPaper
from app.services.citation import paper_citations
from app.web.research_deps import load_project, project_error_response

router = APIRouter()


@router.get("/projects/{project_id}/bibliography.bib")
def project_bibtex(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        rows = list(
            session.execute(
                select(Paper)
                .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
                .where(ProjectPaper.project_id == project.id)
            ).all()
        )
        blocks = [paper_citations(row[0]).get("bibtex", "") for row in rows]
    return PlainTextResponse("\n\n".join(item for item in blocks if item), media_type="application/x-bibtex")


@router.get("/projects/{project_id}/export")
def project_export_page_redirect(request: Request, project_id: int):
    return RedirectResponse(f"/projects/{project_id}/report", status_code=302)
