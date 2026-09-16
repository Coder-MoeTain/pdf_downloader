"""Research project list, overview, questions, strategies, and paper membership."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from app.database.connection import session_scope
from app.database.models import Paper
from app.database.research_models import (
    PROJECT_STATUSES,
    REVIEW_TYPES,
    ProjectActivity,
    ProjectPaper,
    ResearchQuestion,
    SearchStrategy,
)
from app.services.project_service import (
    add_papers_to_project,
    add_research_question,
    archive_project,
    create_project,
    list_project_members,
    list_user_projects,
    overview_kpis,
    recent_project_papers,
    review_type_label,
    save_strategy,
    update_project,
)
from app.web.dependencies import _ctx, _request_user_id, templates
from app.web.flash import set_flash
from app.web.research_deps import load_project, project_ctx, project_error_response

router = APIRouter()


@router.get("/projects", response_class=HTMLResponse)
def projects_index(request: Request):
    user_id = _request_user_id(request)
    if user_id is None:
        return RedirectResponse("/login?next=/projects", status_code=302)
    with session_scope() as session:
        projects = list_user_projects(session, user_id)
        cards = []
        for project in projects:
            kpis = overview_kpis(session, project)
            cards.append({"project": project, "kpis": kpis, "review_label": review_type_label(project.review_type)})
    return templates.TemplateResponse(
        request,
        "projects/index.html",
        _ctx(request, project_cards=cards, review_types=REVIEW_TYPES, statuses=PROJECT_STATUSES),
    )


@router.post("/projects")
def projects_create(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    research_domain: str = Form(""),
    review_type: str = Form("literature_review"),
):
    user_id = _request_user_id(request)
    if user_id is None:
        return RedirectResponse("/login?next=/projects", status_code=302)
    try:
        with session_scope() as session:
            project = create_project(
                session,
                user_id,
                title=title,
                description=description,
                research_domain=research_domain,
                review_type=review_type,
            )
            project_id = project.id
    except ValueError as exc:
        set_flash(request, str(exc), "danger")
        return RedirectResponse("/projects", status_code=303)
    set_flash(request, "Project created.", "success")
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_overview(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        from app.database.research_models import ResearchProject

        live = session.get(ResearchProject, project.id)
        kpis = overview_kpis(session, live)
        recent = recent_project_papers(session, project.id)
        questions = list(
            session.scalars(
                select(ResearchQuestion)
                .where(ResearchQuestion.project_id == project.id, ResearchQuestion.archived.is_(False))
                .order_by(ResearchQuestion.position)
            ).all()
        )
        strategies = list(session.scalars(select(SearchStrategy).where(SearchStrategy.project_id == project.id)).all())
        activity = list(
            session.scalars(
                select(ProjectActivity)
                .where(ProjectActivity.project_id == project.id)
                .order_by(ProjectActivity.created_at.desc())
                .limit(12)
            ).all()
        )
        members = list_project_members(session, project.id)
        funnel = [
            ("Identified", kpis["identified"]),
            ("Deduplicated", max(0, kpis["identified"] - kpis["duplicates"])),
            ("Title/abstract screened", kpis["screened"]),
            ("Full-text screened", kpis["included"] + kpis["excluded"]),
            ("Included", kpis["included"]),
        ]
    return templates.TemplateResponse(
        request,
        "projects/overview.html",
        project_ctx(
            request,
            project,
            member,
            active_tab="overview",
            kpis=kpis,
            recent_papers=recent,
            questions=questions,
            strategies=strategies,
            activity=activity,
            members=members,
            funnel=funnel,
            review_types=REVIEW_TYPES,
            statuses=PROJECT_STATUSES,
        ),
    )


@router.post("/projects/{project_id}/settings")
def project_settings(
    request: Request,
    project_id: int,
    title: str = Form(...),
    description: str = Form(""),
    research_domain: str = Form(""),
    review_type: str = Form("literature_review"),
    status: str = Form("planning"),
    dual_screening: str = Form(""),
    blinded_screening: str = Form(""),
    allow_remote_ai: str = Form(""),
):
    try:
        project, member, user_id = load_project(request, project_id, min_role="owner")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        from app.database.research_models import ResearchProject

        live = session.get(ResearchProject, project.id)
        update_project(
            session,
            live,
            user_id,
            title=title,
            description=description,
            research_domain=research_domain,
            review_type=review_type,
            status=status,
            dual_screening=dual_screening.lower() in {"1", "on", "true"},
            blinded_screening=blinded_screening.lower() in {"1", "on", "true"},
            allow_remote_ai=allow_remote_ai.lower() in {"1", "on", "true"},
        )
    set_flash(request, "Project settings saved.", "success")
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/archive")
def project_archive(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id, min_role="owner")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        from app.database.research_models import ResearchProject

        live = session.get(ResearchProject, project.id)
        archive_project(session, live, user_id)
    set_flash(request, "Project archived.", "success")
    return RedirectResponse("/projects", status_code=303)


@router.post("/projects/{project_id}/questions")
def project_add_question(
    request: Request,
    project_id: int,
    question: str = Form(...),
    description: str = Form(""),
    code: str = Form(""),
):
    try:
        project, member, user_id = load_project(request, project_id, min_role="editor")
    except Exception as exc:
        return project_error_response(request, exc)
    try:
        with session_scope() as session:
            from app.database.research_models import ResearchProject

            live = session.get(ResearchProject, project.id)
            add_research_question(session, live, user_id, question, description=description, code=code)
    except ValueError as exc:
        set_flash(request, str(exc), "danger")
    else:
        set_flash(request, "Research question added.", "success")
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/questions/{question_id}/archive")
def project_archive_question(request: Request, project_id: int, question_id: int):
    try:
        project, member, user_id = load_project(request, project_id, min_role="editor")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        row = session.get(ResearchQuestion, question_id)
        if row and row.project_id == project.id:
            row.archived = True
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/strategies")
def project_save_strategy(
    request: Request,
    project_id: int,
    name: str = Form(""),
    query: str = Form(...),
    synonyms: str = Form(""),
    boolean_string: str = Form(""),
    year_from: str = Form(""),
    year_to: str = Form(""),
    language: str = Form(""),
    document_type: str = Form(""),
    open_access_only: str = Form(""),
    providers: str = Form(""),
):
    try:
        project, member, user_id = load_project(request, project_id, min_role="editor")
    except Exception as exc:
        return project_error_response(request, exc)
    try:
        with session_scope() as session:
            from app.database.research_models import ResearchProject

            live = session.get(ResearchProject, project.id)
            save_strategy(
                session,
                live,
                user_id,
                name=name,
                query=query,
                synonyms=synonyms,
                boolean_string=boolean_string,
                year_from=int(year_from) if year_from.strip().isdigit() else None,
                year_to=int(year_to) if year_to.strip().isdigit() else None,
                language=language,
                document_type=document_type,
                open_access_only=open_access_only.lower() in {"1", "on", "true"},
                providers=[part.strip() for part in providers.split(",") if part.strip()],
            )
    except ValueError as exc:
        set_flash(request, str(exc), "danger")
    else:
        set_flash(request, "Search strategy saved.", "success")
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.get("/projects/{project_id}/papers", response_class=HTMLResponse)
def project_papers(request: Request, project_id: int):
    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        rows = list(
            session.execute(
                select(ProjectPaper, Paper)
                .join(Paper, Paper.id == ProjectPaper.paper_id)
                .where(ProjectPaper.project_id == project.id)
                .order_by(ProjectPaper.id.desc())
            ).all()
        )
    return templates.TemplateResponse(
        request,
        "projects/papers.html",
        project_ctx(request, project, member, active_tab="papers", paper_rows=rows),
    )


@router.post("/projects/{project_id}/papers")
async def project_add_papers(request: Request, project_id: int, paper_ids: str = Form("")):
    try:
        project, member, user_id = load_project(request, project_id, min_role="editor")
    except Exception as exc:
        return project_error_response(request, exc)
    ids: list[int] = []
    if paper_ids.strip().startswith("["):
        import json

        try:
            ids = [int(item) for item in json.loads(paper_ids)]
        except Exception:
            ids = []
    else:
        ids = [int(part) for part in paper_ids.replace(",", " ").split() if part.isdigit()]
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
        ids = [int(item) for item in payload.get("paper_ids") or [] if str(item).isdigit()]
    with session_scope() as session:
        from app.database.research_models import ResearchProject

        live = session.get(ResearchProject, project.id)
        result = add_papers_to_project(session, live, user_id, ids)
    if request.headers.get("accept") == "application/json" or request.headers.get("content-type", "").startswith(
        "application/json"
    ):
        return JSONResponse({"ok": True, **result})
    set_flash(request, f"Added {result['added']} paper(s). {result['skipped']} already in project.", "success")
    return RedirectResponse(f"/projects/{project_id}/papers", status_code=303)


@router.get("/projects/{project_id}/report", response_class=HTMLResponse)
def project_export_page(request: Request, project_id: int):
    from app.services.report_builder import markdown_report, snapshot_payload
    from app.web.research_deps import load_project, project_ctx, project_error_response

    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        from app.database.research_models import ResearchProject

        live = session.get(ResearchProject, project.id)
        payload = snapshot_payload(session, live)
        preview = markdown_report(payload)
    return templates.TemplateResponse(
        request,
        "projects/export.html",
        project_ctx(request, project, member, active_tab="export", preview=preview),
    )


@router.get("/projects/{project_id}/snapshot.zip")
def project_snapshot(request: Request, project_id: int):
    from fastapi.responses import Response

    from app.services.report_builder import snapshot_payload, snapshot_zip
    from app.web.research_deps import load_project, project_error_response

    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        from app.database.research_models import ResearchProject

        live = session.get(ResearchProject, project.id)
        payload = snapshot_payload(session, live)
        data = snapshot_zip(payload)
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={project.slug}-snapshot.zip"},
    )


@router.get("/projects/{project_id}/report.md")
def project_report_md(request: Request, project_id: int):
    from fastapi.responses import PlainTextResponse

    from app.services.report_builder import markdown_report, snapshot_payload
    from app.web.research_deps import load_project, project_error_response

    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        from app.database.research_models import ResearchProject

        live = session.get(ResearchProject, project.id)
        text = markdown_report(snapshot_payload(session, live))
    return PlainTextResponse(text, headers={"Content-Disposition": "attachment; filename=research-report.md"})


@router.get("/projects/{project_id}/report.html")
def project_report_html(request: Request, project_id: int):
    from fastapi.responses import HTMLResponse as FileHtml

    from app.services.report_builder import html_report, snapshot_payload
    from app.web.research_deps import load_project, project_error_response

    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        from app.database.research_models import ResearchProject

        live = session.get(ResearchProject, project.id)
        text = html_report(snapshot_payload(session, live))
    return FileHtml(text, headers={"Content-Disposition": "attachment; filename=research-report.html"})


@router.get("/projects/{project_id}/report.tex")
def project_report_tex(request: Request, project_id: int):
    from fastapi.responses import PlainTextResponse

    from app.services.report_builder import latex_report, snapshot_payload
    from app.web.research_deps import load_project, project_error_response

    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        from app.database.research_models import ResearchProject

        live = session.get(ResearchProject, project.id)
        text = latex_report(snapshot_payload(session, live))
    return PlainTextResponse(text, headers={"Content-Disposition": "attachment; filename=research-report.tex"})


@router.get("/projects/{project_id}/report.docx")
def project_report_docx(request: Request, project_id: int):
    from fastapi.responses import Response

    from app.services.report_builder import docx_report, snapshot_payload
    from app.web.research_deps import load_project, project_error_response

    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        from app.database.research_models import ResearchProject

        live = session.get(ResearchProject, project.id)
        data = docx_report(snapshot_payload(session, live))
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=research-report.docx"},
    )


@router.get("/projects/{project_id}/zotero.json")
def project_zotero_json(request: Request, project_id: int):
    import json

    from fastapi.responses import Response
    from sqlalchemy import select

    from app.database.models import Paper
    from app.database.research_models import ProjectPaper
    from app.services.zotero_service import items_from_papers
    from app.web.research_deps import load_project, project_error_response

    try:
        project, member, user_id = load_project(request, project_id)
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        from sqlalchemy.orm import selectinload

        from app.database.models import Paper, PaperAuthor

        papers = [
            row[0]
            for row in session.execute(
                select(Paper)
                .options(selectinload(Paper.authors).selectinload(PaperAuthor.author))
                .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
                .where(ProjectPaper.project_id == project.id)
                .where((ProjectPaper.screening_stage == "included") | (ProjectPaper.decision == "include"))
            ).all()
        ]
        payload = items_from_papers(papers)
    return Response(
        json.dumps(payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=zotero-items.json"},
    )


@router.post("/projects/{project_id}/zotero/push")
async def project_zotero_push(request: Request, project_id: int):
    from sqlalchemy import select

    from app.database.models import Paper
    from app.database.research_models import ProjectPaper
    from app.exceptions import CyberScholarError
    from app.services.zotero_service import items_from_papers, push_items
    from app.web.flash import set_flash
    from app.web.research_deps import load_project, project_error_response

    try:
        project, member, user_id = load_project(request, project_id, min_role="editor")
    except Exception as exc:
        return project_error_response(request, exc)
    with session_scope() as session:
        from sqlalchemy.orm import selectinload

        from app.database.models import Paper, PaperAuthor

        papers = [
            row[0]
            for row in session.execute(
                select(Paper)
                .options(selectinload(Paper.authors).selectinload(PaperAuthor.author))
                .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
                .where(ProjectPaper.project_id == project.id)
                .where((ProjectPaper.screening_stage == "included") | (ProjectPaper.decision == "include"))
            ).all()
        ]
        for paper in papers:
            session.expunge(paper)
        items = items_from_papers(papers)
    try:
        result = await push_items(items)
        set_flash(request, f"Pushed {result.get('created', len(items))} item(s) to Zotero.", "success")
    except CyberScholarError as exc:
        set_flash(request, exc.public_message, "warning")
    return RedirectResponse(f"/projects/{project_id}/report", status_code=303)
