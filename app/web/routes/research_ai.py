"""Ask this paper / ask research. Answers require retrieved evidence."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.database.connection import session_scope
from app.database.models import Paper
from app.database.research_models import ProjectPaper, ResearchProject
from app.services.research_rag import answer_from_evidence, retrieve
from app.web.dependencies import templates
from app.web.research_deps import load_project, project_ctx, project_error_response

router = APIRouter()

_NO_EVIDENCE = "I could not find sufficient evidence in this paper."


@router.get("/projects/{project_id}/ask", response_class=HTMLResponse)
def ask_page(request: Request, project_id: int, q: str = "", paper_id: int | None = None):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    result = None
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        paper_ids = [
            row.paper_id for row in session.scalars(select_project_papers(project.id)).all() if not row.is_duplicate
        ]
        if paper_id:
            paper_ids = [paper_id]
        if q.strip():
            hits = retrieve(session, q, paper_ids=paper_ids, limit=8)
            result = answer_from_evidence(q, hits, allow_remote=bool(live and live.allow_remote_ai))
    return templates.TemplateResponse(
        request,
        "projects/ask.html",
        project_ctx(request, project, member, active_tab="ask", question=q, result=result, focus_paper_id=paper_id),
    )


def select_project_papers(project_id: int):
    from sqlalchemy import select

    return select(ProjectPaper).where(ProjectPaper.project_id == project_id)


@router.post("/api/projects/{project_id}/ask")
def ask_api(request: Request, project_id: int, question: str = Form(...), paper_id: int | None = Form(None)):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc, json_mode=True)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        ids = [row.paper_id for row in session.scalars(select_project_papers(project.id)).all()]
        if paper_id:
            ids = [paper_id]
        hits = retrieve(session, question, paper_ids=ids, limit=8)
        result = answer_from_evidence(question, hits, allow_remote=bool(live and live.allow_remote_ai))
    return JSONResponse({"ok": True, **result})


@router.post("/api/papers/{paper_id}/ask")
def ask_paper_api(request: Request, paper_id: int, question: str = Form(...)):
    from app.web.dependencies import _request_user_id

    if _request_user_id(request) is None:
        return JSONResponse({"ok": False, "error": "Sign in required"}, status_code=401)
    with session_scope() as session:
        if session.get(Paper, paper_id) is None:
            return JSONResponse({"ok": False, "error": "Paper not found"}, status_code=404)
        hits = retrieve(session, question, paper_ids=[paper_id], limit=6)
        result = answer_from_evidence(question, hits, allow_remote=False)
    if not hits:
        result["answer"] = _NO_EVIDENCE
        result["supported"] = False
    return JSONResponse({"ok": True, **result})
