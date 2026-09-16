"""Research project CRUD, membership, and authorization."""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import Download, Paper, User
from app.database.research_models import (
    MEMBER_ROLES,
    PROJECT_STATUSES,
    REVIEW_TYPES,
    EligibilityCriterion,
    ExclusionReason,
    ExtractionField,
    ExtractionSchema,
    ProjectActivity,
    ProjectMember,
    ProjectPaper,
    QualityChecklist,
    QualityQuestion,
    ResearchProject,
    ResearchQuestion,
    SearchRun,
    SearchStrategy,
)
from app.exceptions import AuthorizationError, CyberScholarError
from app.utils.time import utc_now

ROLE_RANK = {"viewer": 1, "reviewer": 2, "editor": 3, "owner": 4}

DEFAULT_EXCLUSION = (
    ("wrong_topic", "Wrong topic"),
    ("wrong_population", "Wrong population"),
    ("wrong_methodology", "Wrong methodology"),
    ("duplicate", "Duplicate"),
    ("no_fulltext", "Full text unavailable"),
    ("not_peer_reviewed", "Not peer reviewed"),
    ("outside_date", "Outside date range"),
    ("not_primary", "Not primary study"),
    ("other", "Other"),
)

DEFAULT_QUALITY = (
    ("Q1", "Is the research objective clearly stated?"),
    ("Q2", "Is the dataset described?"),
    ("Q3", "Is the methodology reproducible?"),
    ("Q4", "Are evaluation metrics appropriate?"),
    ("Q5", "Are limitations discussed?"),
)

DEFAULT_EXTRACTION = (
    ("research_problem", "Research problem"),
    ("research_objective", "Research objective"),
    ("methodology", "Methodology"),
    ("study_design", "Study design"),
    ("datasets", "Datasets"),
    ("dataset_size", "Dataset size"),
    ("algorithms", "Algorithms"),
    ("models", "Models"),
    ("features", "Features"),
    ("experimental_environment", "Experimental environment"),
    ("metrics", "Metrics"),
    ("accuracy", "Accuracy"),
    ("precision", "Precision"),
    ("recall", "Recall"),
    ("f1_score", "F1 score"),
    ("auc", "AUC"),
    ("main_findings", "Main findings"),
    ("limitations", "Limitations"),
    ("future_work", "Future work"),
    ("software_tools", "Software tools"),
    ("code_url", "Code URL"),
    ("dataset_url", "Dataset URL"),
    ("funding", "Funding"),
    ("country", "Country"),
    ("institution", "Institution"),
)

DEFAULT_INCLUSION = (
    ("I1", "Primary research study relevant to the project topic"),
    ("I2", "Peer-reviewed or recognized preprint with extractable methods"),
)
DEFAULT_EXCLUSION_CRITERIA = (
    ("E1", "Not relevant to the stated research questions"),
    ("E2", "Insufficient methodological detail to extract"),
)


class ProjectNotFound(CyberScholarError):
    public_message = "Project not found."
    status_code = 404


class ProjectPermissionError(AuthorizationError):
    public_message = "You do not have access to this project."
    status_code = 403


def _slugify(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:80] or "project"
    return f"{base}-{secrets.token_hex(3)}"


def log_activity(
    session: Session,
    project_id: int,
    actor_user_id: int | None,
    action: str,
    *,
    target_type: str = "",
    target_id: str = "",
    detail: str = "",
) -> None:
    session.add(
        ProjectActivity(
            project_id=project_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id or ""),
            detail=detail[:2000],
        )
    )


def member_for(session: Session, project_id: int, user_id: int) -> ProjectMember | None:
    return session.scalar(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
    )


def can_role(role: str, minimum: str) -> bool:
    return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(minimum, 99)


def get_accessible_project(
    session: Session, project_id: int, user_id: int, *, min_role: str = "viewer"
) -> tuple[ResearchProject, ProjectMember]:
    project = session.get(ResearchProject, project_id)
    if project is None:
        raise ProjectNotFound("Project not found.")
    member = member_for(session, project_id, user_id)
    if member is None:
        raise ProjectPermissionError("You do not have access to this project.")
    if not can_role(member.role, min_role):
        raise ProjectPermissionError("This action requires a higher project role.")
    return project, member


def list_user_projects(session: Session, user_id: int, *, include_archived: bool = False) -> list[ResearchProject]:
    stmt = (
        select(ResearchProject)
        .join(ProjectMember, ProjectMember.project_id == ResearchProject.id)
        .where(ProjectMember.user_id == user_id)
        .order_by(ResearchProject.updated_at.desc())
    )
    if not include_archived:
        stmt = stmt.where(ResearchProject.status != "archived")
    return list(session.scalars(stmt).unique().all())


def _seed_project(session: Session, project: ResearchProject) -> None:
    for code, label in DEFAULT_EXCLUSION:
        session.add(ExclusionReason(project_id=project.id, code=code, label=label))
    for position, (code, description) in enumerate(DEFAULT_INCLUSION, start=1):
        session.add(
            EligibilityCriterion(
                project_id=project.id, type="inclusion", code=code, description=description, position=position
            )
        )
    for position, (code, description) in enumerate(DEFAULT_EXCLUSION_CRITERIA, start=1):
        session.add(
            EligibilityCriterion(
                project_id=project.id, type="exclusion", code=code, description=description, position=position
            )
        )
    checklist = QualityChecklist(project_id=project.id, name="Quality assessment")
    session.add(checklist)
    session.flush()
    for position, (code, prompt) in enumerate(DEFAULT_QUALITY, start=1):
        session.add(QualityQuestion(checklist_id=checklist.id, code=code, prompt=prompt, position=position))
    schema = ExtractionSchema(project_id=project.id)
    session.add(schema)
    session.flush()
    for position, (key, label) in enumerate(DEFAULT_EXTRACTION, start=1):
        session.add(ExtractionField(schema_id=schema.id, key=key, label=label, position=position))


def create_project(
    session: Session,
    user_id: int,
    *,
    title: str,
    description: str = "",
    research_domain: str = "",
    review_type: str = "literature_review",
) -> ResearchProject:
    clean = (title or "").strip()
    if len(clean) < 3:
        raise ValueError("Project title must be at least 3 characters.")
    kind = review_type if review_type in REVIEW_TYPES else "literature_review"
    project = ResearchProject(
        user_id=user_id,
        title=clean,
        slug=_slugify(clean),
        description=(description or "").strip(),
        research_domain=(research_domain or "").strip(),
        review_type=kind,
        status="planning",
    )
    session.add(project)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user_id, role="owner"))
    _seed_project(session, project)
    log_activity(session, project.id, user_id, "project_created", target_type="project", target_id=str(project.id))
    return project


def update_project(
    session: Session,
    project: ResearchProject,
    user_id: int,
    *,
    title: str | None = None,
    description: str | None = None,
    research_domain: str | None = None,
    review_type: str | None = None,
    status: str | None = None,
    dual_screening: bool | None = None,
    blinded_screening: bool | None = None,
    allow_remote_ai: bool | None = None,
) -> ResearchProject:
    if title is not None:
        clean = title.strip()
        if len(clean) < 3:
            raise ValueError("Project title must be at least 3 characters.")
        project.title = clean
    if description is not None:
        project.description = description.strip()
    if research_domain is not None:
        project.research_domain = research_domain.strip()
    if review_type is not None and review_type in REVIEW_TYPES:
        project.review_type = review_type
    if status is not None and status in PROJECT_STATUSES:
        project.status = status
    if dual_screening is not None:
        project.dual_screening = bool(dual_screening)
    if blinded_screening is not None:
        project.blinded_screening = bool(blinded_screening)
    if allow_remote_ai is not None:
        project.allow_remote_ai = bool(allow_remote_ai)
    project.updated_at = utc_now()
    log_activity(session, project.id, user_id, "project_updated", target_type="project", target_id=str(project.id))
    return project


def archive_project(session: Session, project: ResearchProject, user_id: int) -> ResearchProject:
    project.status = "archived"
    project.updated_at = utc_now()
    log_activity(session, project.id, user_id, "project_archived", target_type="project", target_id=str(project.id))
    return project


def add_member(session: Session, project: ResearchProject, actor_id: int, user_id: int, role: str) -> ProjectMember:
    if role not in MEMBER_ROLES or role == "owner":
        raise ValueError("Invalid project role.")
    existing = member_for(session, project.id, user_id)
    if existing:
        existing.role = role
        member = existing
    else:
        member = ProjectMember(project_id=project.id, user_id=user_id, role=role)
        session.add(member)
    log_activity(
        session, project.id, actor_id, "member_changed", target_type="user", target_id=str(user_id), detail=role
    )
    return member


def add_research_question(
    session: Session, project: ResearchProject, user_id: int, question: str, *, description: str = "", code: str = ""
) -> ResearchQuestion:
    text = (question or "").strip()
    if not text:
        raise ValueError("Research question is required.")
    count = (
        session.scalar(
            select(func.count(ResearchQuestion.id)).where(
                ResearchQuestion.project_id == project.id, ResearchQuestion.archived.is_(False)
            )
        )
        or 0
    )
    rq = ResearchQuestion(
        project_id=project.id,
        code=(code or f"RQ{count + 1}").strip()[:16],
        question=text,
        description=(description or "").strip(),
        position=count,
    )
    session.add(rq)
    session.flush()
    log_activity(session, project.id, user_id, "question_added", target_type="question", target_id=str(rq.id))
    return rq


def save_strategy(
    session: Session,
    project: ResearchProject,
    user_id: int,
    *,
    name: str,
    query: str,
    strategy_id: int | None = None,
    expanded_query: str = "",
    synonyms: str = "",
    boolean_string: str = "",
    providers: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    language: str = "",
    document_type: str = "",
    open_access_only: bool = False,
) -> SearchStrategy:
    title = (name or "").strip() or (query or "").strip()[:80] or "Search strategy"
    q = (query or "").strip()
    if not q:
        raise ValueError("Search query is required.")
    config = json.dumps({"providers": providers or []})
    if strategy_id:
        row = session.get(SearchStrategy, strategy_id)
        if row is None or row.project_id != project.id:
            raise ValueError("Search strategy not found.")
    else:
        row = SearchStrategy(project_id=project.id)
        session.add(row)
    row.name = title
    row.query = q
    row.expanded_query = expanded_query
    row.synonyms = synonyms
    row.boolean_string = boolean_string
    row.provider_config_json = config
    row.year_from = year_from
    row.year_to = year_to
    row.language = language
    row.document_type = document_type
    row.open_access_only = bool(open_access_only)
    session.flush()
    log_activity(session, project.id, user_id, "strategy_saved", target_type="strategy", target_id=str(row.id))
    return row


def record_search_run(
    session: Session,
    project_id: int,
    *,
    strategy_id: int | None,
    query: str,
    provider: str,
    filters: dict[str, Any],
    result_count: int,
) -> SearchRun:
    run = SearchRun(
        project_id=project_id,
        strategy_id=strategy_id,
        query=query,
        provider=provider,
        filters_json=json.dumps(filters or {}),
        result_count=int(result_count),
    )
    session.add(run)
    if strategy_id:
        strategy = session.get(SearchStrategy, strategy_id)
        if strategy is not None:
            strategy.last_run_at = utc_now()
            strategy.last_result_count = int(result_count)
    return run


def add_papers_to_project(
    session: Session,
    project: ResearchProject,
    user_id: int,
    paper_ids: list[int],
    *,
    strategy_id: int | None = None,
) -> dict[str, int]:
    added = 0
    skipped = 0
    for paper_id in paper_ids:
        paper = session.get(Paper, int(paper_id))
        if paper is None:
            skipped += 1
            continue
        existing = session.scalar(
            select(ProjectPaper).where(ProjectPaper.project_id == project.id, ProjectPaper.paper_id == paper.id)
        )
        if existing:
            skipped += 1
            continue
        session.add(
            ProjectPaper(
                project_id=project.id,
                paper_id=paper.id,
                added_by_user_id=user_id,
                search_strategy_id=strategy_id,
            )
        )
        added += 1
    if added:
        project.updated_at = utc_now()
        if project.status == "planning":
            project.status = "searching"
        log_activity(
            session,
            project.id,
            user_id,
            "papers_added",
            target_type="project",
            target_id=str(project.id),
            detail=f"{added} papers",
        )
    return {"added": added, "skipped": skipped}


def overview_kpis(session: Session, project: ResearchProject) -> dict[str, Any]:
    papers = list(session.scalars(select(ProjectPaper).where(ProjectPaper.project_id == project.id)).all())
    identified = len(papers)
    duplicates = sum(1 for row in papers if row.is_duplicate or row.decision_reason == "duplicate")
    screened = sum(1 for row in papers if row.decision != "pending")
    included = sum(
        1
        for row in papers
        if row.decision == "include"
        and row.screening_stage in {"full_text", "included"}
        or row.screening_stage == "included"
    )
    # Count full-text includes plus title-abstract includes that have not been advanced yet
    included = sum(1 for row in papers if row.decision == "include" and not row.is_duplicate)
    excluded = sum(1 for row in papers if row.decision == "exclude")
    paper_ids = [row.paper_id for row in papers]
    pdfs = 0
    extracted = 0
    quality = 0
    if paper_ids:
        pdfs = int(
            session.scalar(
                select(func.count(func.distinct(Download.paper_id))).where(
                    Download.paper_id.in_(paper_ids),
                    Download.status == "DOWNLOADED",
                    Download.local_path.is_not(None),
                )
            )
            or 0
        )
        from app.database.research_models import ExtractedValue, QualityAssessment

        extracted = int(
            session.scalar(
                select(func.count(func.distinct(ExtractedValue.project_paper_id))).where(
                    ExtractedValue.project_paper_id.in_([row.id for row in papers])
                )
            )
            or 0
        )
        quality = int(
            session.scalar(
                select(func.count(QualityAssessment.id)).where(
                    QualityAssessment.project_paper_id.in_([row.id for row in papers])
                )
            )
            or 0
        )
    return {
        "identified": identified,
        "duplicates": duplicates,
        "screened": screened,
        "included": included,
        "excluded": excluded,
        "pdfs": pdfs,
        "extracted": extracted,
        "quality": quality,
        "pending": sum(1 for row in papers if row.decision == "pending" and not row.is_duplicate),
        "maybe": sum(1 for row in papers if row.decision == "maybe"),
    }


def recent_project_papers(session: Session, project_id: int, *, limit: int = 8) -> list[tuple[ProjectPaper, Paper]]:
    stmt = (
        select(ProjectPaper, Paper)
        .join(Paper, Paper.id == ProjectPaper.paper_id)
        .where(ProjectPaper.project_id == project_id)
        .order_by(ProjectPaper.created_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).all())


def list_project_members(session: Session, project_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.role.asc(), User.name.asc())
    ).all()
    return [
        {"user_id": user.id, "name": user.name or user.email, "email": user.email, "role": member.role}
        for member, user in rows
    ]


def review_type_label(value: str) -> str:
    return {
        "literature_review": "Literature review",
        "systematic_review": "Systematic literature review",
        "scoping_review": "Scoping review",
        "mapping_study": "Mapping study",
        "meta_analysis": "Meta-analysis",
        "general_research": "General research",
    }.get(value, value.replace("_", " ").title())


def workspace_summary(session: Session, user_id: int) -> dict[str, Any]:
    projects = list_user_projects(session, user_id)
    screening_pending = 0
    if projects:
        screening_pending = (
            session.scalar(
                select(func.count(ProjectPaper.id)).where(
                    ProjectPaper.project_id.in_([project.id for project in projects]),
                    ProjectPaper.decision == "pending",
                    ProjectPaper.is_duplicate.is_(False),
                )
            )
            or 0
        )
    return {
        "projects": projects[:8],
        "project_count": len(projects),
        "screening_pending": int(screening_pending),
        "active": len([project for project in projects if project.status not in {"completed", "archived"}]),
    }
