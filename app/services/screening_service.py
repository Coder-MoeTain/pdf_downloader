"""Title/abstract and full-text screening, including dual-reviewer conflicts."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Paper
from app.database.research_models import (
    SCREENING_DECISIONS,
    SCREENING_STAGES,
    EligibilityCriterion,
    ExclusionReason,
    ProjectPaper,
    ResearchProject,
    ScreeningDecision,
)
from app.services.project_service import log_activity
from app.utils.time import utc_now


def list_exclusion_reasons(session: Session, project_id: int) -> list[ExclusionReason]:
    return list(
        session.scalars(
            select(ExclusionReason)
            .where(ExclusionReason.project_id == project_id, ExclusionReason.active.is_(True))
            .order_by(ExclusionReason.id)
        ).all()
    )


def list_criteria(session: Session, project_id: int) -> list[EligibilityCriterion]:
    return list(
        session.scalars(
            select(EligibilityCriterion)
            .where(EligibilityCriterion.project_id == project_id, EligibilityCriterion.active.is_(True))
            .order_by(EligibilityCriterion.type.desc(), EligibilityCriterion.position, EligibilityCriterion.id)
        ).all()
    )


def save_criterion(
    session: Session, project_id: int, *, kind: str, code: str, description: str, criterion_id: int | None = None
) -> EligibilityCriterion:
    if kind not in {"inclusion", "exclusion"}:
        raise ValueError("Criterion type must be inclusion or exclusion.")
    text = (description or "").strip()
    if not text:
        raise ValueError("Criterion description is required.")
    if criterion_id:
        row = session.get(EligibilityCriterion, criterion_id)
        if row is None or row.project_id != project_id:
            raise ValueError("Criterion not found.")
    else:
        row = EligibilityCriterion(project_id=project_id, type=kind)
        session.add(row)
    row.type = kind
    row.code = (code or "").strip()[:16] or ("I" if kind == "inclusion" else "E")
    row.description = text
    return row


def _load_paper(session: Session, project_paper_id: int) -> tuple[ProjectPaper, Paper]:
    row = session.get(ProjectPaper, project_paper_id)
    if row is None:
        raise ValueError("Project paper not found.")
    paper = session.get(Paper, row.paper_id)
    if paper is None:
        raise ValueError("Paper not found.")
    return row, paper


def queue_for_stage(
    session: Session, project: ResearchProject, stage: str, *, include_maybe: bool = True
) -> list[ProjectPaper]:
    stage = stage if stage in SCREENING_STAGES else "title_abstract"
    stmt = select(ProjectPaper).where(ProjectPaper.project_id == project.id, ProjectPaper.is_duplicate.is_(False))
    if stage == "title_abstract":
        stmt = stmt.where(ProjectPaper.screening_stage == "title_abstract")
        if include_maybe:
            stmt = stmt.where(ProjectPaper.decision.in_(["pending", "maybe"]))
        else:
            stmt = stmt.where(ProjectPaper.decision == "pending")
    else:
        stmt = stmt.where(ProjectPaper.screening_stage == "full_text")
        if include_maybe:
            stmt = stmt.where(ProjectPaper.decision.in_(["pending", "maybe"]))
        else:
            stmt = stmt.where(ProjectPaper.decision == "pending")
    return list(session.scalars(stmt.order_by(ProjectPaper.id)).all())


def next_in_queue(
    session: Session, project: ResearchProject, stage: str, after_id: int | None = None
) -> ProjectPaper | None:
    rows = queue_for_stage(session, project, stage)
    if not rows:
        return None
    if after_id is None:
        return rows[0]
    for index, row in enumerate(rows):
        if row.id == after_id and index + 1 < len(rows):
            return rows[index + 1]
    return rows[0]


def _apply_consensus(project: ResearchProject, row: ProjectPaper, decision: str, reason: str, reviewer_id: int) -> None:
    row.decision = decision
    row.decision_reason = reason if decision == "exclude" else ""
    row.reviewer_id = reviewer_id
    row.updated_at = utc_now()
    if decision == "include" and row.screening_stage == "title_abstract":
        row.screening_stage = "full_text"
        row.decision = "pending"
        row.decision_reason = ""
    elif decision == "include" and row.screening_stage == "full_text":
        row.screening_stage = "included"
    elif decision == "exclude":
        row.screening_stage = "excluded" if row.screening_stage == "full_text" else row.screening_stage
        if reason == "duplicate":
            row.is_duplicate = True


def record_decision(
    session: Session,
    project: ResearchProject,
    project_paper_id: int,
    reviewer_id: int,
    *,
    stage: str,
    decision: str,
    reason: str = "",
) -> dict[str, Any]:
    if stage not in SCREENING_STAGES:
        raise ValueError("Unknown screening stage.")
    if decision not in SCREENING_DECISIONS or decision == "pending":
        raise ValueError("Decision must be include, exclude, or maybe.")
    if decision == "exclude" and not (reason or "").strip():
        raise ValueError("An exclusion reason is required.")
    row, _paper = _load_paper(session, project_paper_id)
    if row.project_id != project.id:
        raise ValueError("Paper is not in this project.")
    existing = session.scalar(
        select(ScreeningDecision).where(
            ScreeningDecision.project_paper_id == row.id,
            ScreeningDecision.reviewer_id == reviewer_id,
            ScreeningDecision.stage == stage,
        )
    )
    if existing is None:
        existing = ScreeningDecision(
            project_paper_id=row.id, reviewer_id=reviewer_id, stage=stage, decision=decision, reason=reason
        )
        session.add(existing)
    else:
        existing.decision = decision
        existing.reason = reason
        existing.updated_at = utc_now()

    conflict = False
    if project.dual_screening:
        others = list(
            session.scalars(
                select(ScreeningDecision).where(
                    ScreeningDecision.project_paper_id == row.id, ScreeningDecision.stage == stage
                )
            ).all()
        )
        unique_reviewers = {item.reviewer_id for item in others}
        if len(unique_reviewers) >= 2:
            decisions = {item.decision for item in others}
            if len(decisions) > 1:
                conflict = True
            else:
                consensus = others[0].decision
                consensus_reason = next((item.reason for item in others if item.decision == "exclude"), "")
                _apply_consensus(project, row, consensus, consensus_reason, reviewer_id)
        # Independent screening: do not apply a single reviewer's vote as final.
    else:
        _apply_consensus(project, row, decision, reason, reviewer_id)

    if project.status in {"planning", "searching"}:
        project.status = "screening"
    project.updated_at = utc_now()
    log_activity(
        session,
        project.id,
        reviewer_id,
        "screening_decision",
        target_type="project_paper",
        target_id=str(row.id),
        detail=f"{stage}:{decision}",
    )
    return {"project_paper_id": row.id, "decision": row.decision, "stage": row.screening_stage, "conflict": conflict}


def resolve_conflict(
    session: Session,
    project: ResearchProject,
    project_paper_id: int,
    actor_id: int,
    *,
    stage: str,
    decision: str,
    reason: str = "",
) -> ProjectPaper:
    row, _paper = _load_paper(session, project_paper_id)
    if row.project_id != project.id:
        raise ValueError("Paper is not in this project.")
    _apply_consensus(project, row, decision, reason, actor_id)
    log_activity(
        session,
        project.id,
        actor_id,
        "conflict_resolved",
        target_type="project_paper",
        target_id=str(row.id),
        detail=f"{stage}:{decision}",
    )
    return row


def conflict_rows(session: Session, project: ResearchProject, stage: str) -> list[dict[str, Any]]:
    papers = list(session.scalars(select(ProjectPaper).where(ProjectPaper.project_id == project.id)).all())
    out: list[dict[str, Any]] = []
    for row in papers:
        decisions = list(
            session.scalars(
                select(ScreeningDecision).where(
                    ScreeningDecision.project_paper_id == row.id, ScreeningDecision.stage == stage
                )
            ).all()
        )
        if len({item.reviewer_id for item in decisions}) < 2:
            continue
        if len({item.decision for item in decisions}) <= 1:
            continue
        paper = session.get(Paper, row.paper_id)
        out.append(
            {
                "project_paper": row,
                "paper": paper,
                "decisions": decisions,
            }
        )
    return out


def screening_stats(session: Session, project: ResearchProject, stage: str = "title_abstract") -> dict[str, int]:
    rows = list(
        session.scalars(
            select(ProjectPaper).where(ProjectPaper.project_id == project.id, ProjectPaper.is_duplicate.is_(False))
        ).all()
    )
    if stage == "title_abstract":
        pool = [row for row in rows if row.screening_stage in {"title_abstract", "full_text", "included", "excluded"}]
        relevant = [row for row in rows if row.screening_stage == "title_abstract"]
    else:
        relevant = [row for row in rows if row.screening_stage in {"full_text", "included", "excluded"}]
        pool = relevant
    included = sum(1 for row in rows if row.decision == "include" or row.screening_stage == "included")
    excluded = sum(1 for row in relevant if row.decision == "exclude" or row.screening_stage == "excluded")
    maybe = sum(1 for row in relevant if row.decision == "maybe")
    pending = sum(1 for row in relevant if row.decision == "pending")
    total = len(pool) if stage == "title_abstract" else len(relevant)
    screened = total - pending
    return {
        "total": total,
        "screened": screened,
        "included": included,
        "excluded": excluded,
        "maybe": maybe,
        "remaining": pending,
    }


def agreement_stats(session: Session, project: ResearchProject, stage: str) -> dict[str, Any]:
    if not project.dual_screening:
        return {"enabled": False}
    papers = list(session.scalars(select(ProjectPaper).where(ProjectPaper.project_id == project.id)).all())
    paired = 0
    agree = 0
    conflicts = 0
    by_reviewer: dict[int, list[str]] = defaultdict(list)
    for row in papers:
        decisions = list(
            session.scalars(
                select(ScreeningDecision).where(
                    ScreeningDecision.project_paper_id == row.id, ScreeningDecision.stage == stage
                )
            ).all()
        )
        reviewers = {item.reviewer_id: item.decision for item in decisions}
        if len(reviewers) < 2:
            continue
        values = list(reviewers.values())
        paired += 1
        if len(set(values)) == 1:
            agree += 1
        else:
            conflicts += 1
        for reviewer_id, decision in reviewers.items():
            by_reviewer[reviewer_id].append(decision)
    pct = round((agree / paired) * 100) if paired else None
    kappa = _cohens_kappa(list(by_reviewer.values())) if len(by_reviewer) == 2 and paired else None
    return {
        "enabled": True,
        "paired": paired,
        "agree": agree,
        "conflicts": conflicts,
        "agreement_pct": pct,
        "kappa": kappa,
        "kappa_note": (
            "Cohen's kappa compares two reviewers on independently screened papers at this stage."
            if kappa is not None
            else ""
        ),
    }


def _cohens_kappa(reviewer_series: list[list[str]]) -> float | None:
    if len(reviewer_series) != 2:
        return None
    a, b = reviewer_series
    n = min(len(a), len(b))
    if n == 0:
        return None
    a = a[:n]
    b = b[:n]
    labels = sorted(set(a) | set(b))
    po = sum(1 for left, right in zip(a, b, strict=False) if left == right) / n
    pe = 0.0
    for label in labels:
        pe += (a.count(label) / n) * (b.count(label) / n)
    if pe >= 1:
        return 1.0
    return round((po - pe) / (1 - pe), 3)


def visible_decisions_for(
    session: Session, project: ResearchProject, project_paper_id: int, reviewer_id: int, stage: str
) -> list[ScreeningDecision]:
    rows = list(
        session.scalars(
            select(ScreeningDecision).where(
                ScreeningDecision.project_paper_id == project_paper_id, ScreeningDecision.stage == stage
            )
        ).all()
    )
    if project.blinded_screening and project.dual_screening:
        mine = [row for row in rows if row.reviewer_id == reviewer_id]
        others = [row for row in rows if row.reviewer_id != reviewer_id]
        if len({row.reviewer_id for row in rows}) < 2:
            return mine
        return mine + others
    return rows
