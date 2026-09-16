"""Systematic review screening and conflict resolution."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.database.connection import session_scope
from app.database.models import Paper
from app.database.research_models import ProjectPaper, ResearchProject
from app.services.screening_service import (
    agreement_stats,
    conflict_rows,
    list_criteria,
    list_exclusion_reasons,
    next_in_queue,
    record_decision,
    resolve_conflict,
    save_criterion,
    screening_stats,
    visible_decisions_for,
)
from app.web.dependencies import templates
from app.web.flash import set_flash
from app.web.research_deps import load_project, project_ctx, project_error_response

router = APIRouter()


def _hydrate(session, project_id: int) -> ResearchProject:
    project = session.get(ResearchProject, project_id)
    assert project is not None
    return project


@router.get("/projects/{project_id}/screening", response_class=HTMLResponse)
def screening_page(request: Request, project_id: int, stage: str = "title_abstract", item: int | None = None):
    try:
        project, member, user_id = load_project(request, project_id, min_role="reviewer")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = _hydrate(session, project.id)
        current = session.get(ProjectPaper, item) if item else next_in_queue(session, live, stage)
        if current and current.project_id != project.id:
            current = None
        paper = session.get(Paper, current.paper_id) if current else None
        if paper is not None:
            paper = session.merge(paper)
            _ = paper.authors
        stats = screening_stats(session, live, stage)
        agreement = agreement_stats(session, live, stage)
        reasons = list_exclusion_reasons(session, project.id)
        criteria = list_criteria(session, project.id)
        queue = [
            row.id
            for row in __import__("app.services.screening_service", fromlist=["queue_for_stage"]).queue_for_stage(
                session, live, stage
            )
        ]
        index = queue.index(current.id) + 1 if current and current.id in queue else 0
        decisions = visible_decisions_for(session, live, current.id, user_id, stage) if current else []
    return templates.TemplateResponse(
        request,
        "projects/screening.html",
        project_ctx(
            request,
            project,
            member,
            active_tab="screening",
            stage=stage,
            current=current,
            paper=paper,
            stats=stats,
            agreement=agreement,
            reasons=reasons,
            criteria=criteria,
            index=index,
            total=stats["total"],
            decisions=decisions,
        ),
    )


@router.post("/projects/{project_id}/screening/{project_paper_id}")
def screening_decide(
    request: Request,
    project_id: int,
    project_paper_id: int,
    stage: str = Form("title_abstract"),
    decision: str = Form(...),
    reason: str = Form(""),
):
    try:
        project, member, user_id = load_project(request, project_id, min_role="reviewer")
    except Exception as exc:
        return project_error_response(request, exc)
    try:
        with session_scope() as session:
            live = _hydrate(session, project.id)
            result = record_decision(
                session, live, project_paper_id, user_id, stage=stage, decision=decision, reason=reason
            )
            nxt = next_in_queue(session, live, stage, after_id=project_paper_id)
            next_id = nxt.id if nxt else None
    except ValueError as exc:
        if request.headers.get("accept") == "application/json":
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        set_flash(request, str(exc), "danger")
        return RedirectResponse(f"/projects/{project_id}/screening?stage={stage}", status_code=303)
    if request.headers.get("accept") == "application/json":
        return JSONResponse({"ok": True, **result, "next_id": next_id})
    target = f"/projects/{project_id}/screening?stage={stage}"
    if next_id:
        target += f"&item={next_id}"
    return RedirectResponse(target, status_code=303)


@router.get("/projects/{project_id}/screening/conflicts", response_class=HTMLResponse)
def screening_conflicts(request: Request, project_id: int, stage: str = "title_abstract"):
    try:
        project, member, user_id = load_project(request, project_id, min_role="editor")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = _hydrate(session, project.id)
        rows = conflict_rows(session, live, stage)
        reasons = list_exclusion_reasons(session, project.id)
    return templates.TemplateResponse(
        request,
        "projects/conflicts.html",
        project_ctx(request, project, member, active_tab="screening", stage=stage, conflicts=rows, reasons=reasons),
    )


@router.post("/projects/{project_id}/screening/conflicts/{project_paper_id}")
def screening_resolve(
    request: Request,
    project_id: int,
    project_paper_id: int,
    stage: str = Form("title_abstract"),
    decision: str = Form(...),
    reason: str = Form(""),
):
    try:
        project, member, user_id = load_project(request, project_id, min_role="editor")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = _hydrate(session, project.id)
        resolve_conflict(session, live, project_paper_id, user_id, stage=stage, decision=decision, reason=reason)
    set_flash(request, "Conflict resolved.", "success")
    return RedirectResponse(f"/projects/{project_id}/screening/conflicts?stage={stage}", status_code=303)


@router.post("/projects/{project_id}/criteria")
def screening_add_criterion(
    request: Request,
    project_id: int,
    kind: str = Form("inclusion"),
    code: str = Form(""),
    description: str = Form(...),
):
    try:
        project, member, user_id = load_project(request, project_id, min_role="editor")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        save_criterion(session, project.id, kind=kind, code=code, description=description)
    return RedirectResponse(f"/projects/{project_id}/screening", status_code=303)
