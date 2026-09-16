"""Project bibliometric analytics, catalogs, reproducibility, and citation graph."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.database.connection import session_scope
from app.database.research_models import ResearchProject
from app.services.bibliometric_service import bibliometric_overview
from app.services.catalog_service import (
    catalog_overview,
    refresh_catalogs,
    reproducibility_rows,
    upsert_reproducibility,
)
from app.services.citation_graph import citation_graph
from app.web.dependencies import templates
from app.web.flash import set_flash
from app.web.research_deps import load_project, project_ctx, project_error_response

router = APIRouter()


@router.get("/projects/{project_id}/analytics", response_class=HTMLResponse)
def project_analytics(request: Request, project_id: int, included: str = "1"):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    included_only = included != "0"
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        stats = bibliometric_overview(session, live, included_only=included_only)
    return templates.TemplateResponse(
        request,
        "projects/analytics.html",
        project_ctx(request, project, member, active_tab="analytics", stats=stats, included_only=included_only),
    )


@router.get("/projects/{project_id}/catalogs", response_class=HTMLResponse)
def project_catalogs(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        catalogs = catalog_overview(session, live)
        repro = reproducibility_rows(session, live)
    return templates.TemplateResponse(
        request,
        "projects/catalogs.html",
        project_ctx(request, project, member, active_tab="catalogs", catalogs=catalogs, repro=repro),
    )


@router.post("/projects/{project_id}/catalogs/refresh")
def project_catalogs_refresh(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id, min_role="editor")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        refresh_catalogs(session, live)
    set_flash(request, "Dataset and algorithm catalogs rebuilt from extracted fields.", "success")
    return RedirectResponse(f"/projects/{project_id}/catalogs", status_code=303)


@router.post("/projects/{project_id}/reproducibility/{item_id}")
def project_reproducibility_save(
    request: Request,
    project_id: int,
    item_id: int,
    code_available: str = Form(""),
    code_url: str = Form(""),
    dataset_available: str = Form(""),
    dataset_url: str = Form(""),
    environment_described: str = Form(""),
    hyperparameters_reported: str = Form(""),
    random_seed_reported: str = Form(""),
    pretrained_model_available: str = Form(""),
    license_available: str = Form(""),
    reproduced_by_user: str = Form(""),
    reproduction_notes: str = Form(""),
):
    try:
        project, member, user_id = load_project(request, project_id, min_role="editor")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        upsert_reproducibility(
            session,
            live,
            item_id,
            code_available=code_available,
            code_url=code_url,
            dataset_available=dataset_available,
            dataset_url=dataset_url,
            environment_described=environment_described,
            hyperparameters_reported=hyperparameters_reported,
            random_seed_reported=random_seed_reported,
            pretrained_model_available=pretrained_model_available,
            license_available=license_available,
            reproduced_by_user=reproduced_by_user,
            reproduction_notes=reproduction_notes,
        )
    set_flash(request, "Reproducibility record saved.", "success")
    return RedirectResponse(f"/projects/{project_id}/catalogs#repro-{item_id}", status_code=303)


@router.get("/projects/{project_id}/graph", response_class=HTMLResponse)
def project_graph(request: Request, project_id: int, included: str = "1"):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    included_only = included != "0"
    return templates.TemplateResponse(
        request,
        "projects/graph.html",
        project_ctx(request, project, member, active_tab="graph", included_only=included_only),
    )


@router.get("/projects/{project_id}/graph.json")
def project_graph_json(request: Request, project_id: int, included: str = "1", expand: int | None = None):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc, json_mode=True)
    with session_scope() as session:
        live = session.get(ResearchProject, project.id)
        payload = citation_graph(session, live, expand_id=expand, included_only=included != "0")
    return JSONResponse(payload)


@router.get("/analytics", response_class=HTMLResponse)
def global_analytics(request: Request):
    from app.web.routes.search import reports_page

    return reports_page(request)
