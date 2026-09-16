"""Evidence grouping, themes, and research-question synthesis."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Paper
from app.database.research_models import EvidenceSource, EvidenceTheme, ResearchQuestion, Theme
from app.services.project_service import log_activity


def add_evidence(
    session: Session,
    project_id: int,
    user_id: int,
    *,
    paper_id: int,
    text: str,
    stance: str = "supporting",
    page_number: int | None = None,
    section: str = "",
    question_id: int | None = None,
    method: str = "manual",
) -> EvidenceSource:
    snippet = (text or "").strip()
    if not snippet:
        raise ValueError("Evidence text is required.")
    if stance not in {"supporting", "conflicting", "inconclusive"}:
        stance = "supporting"
    row = EvidenceSource(
        project_id=project_id,
        paper_id=paper_id,
        research_question_id=question_id,
        page_number=page_number,
        section=section,
        evidence_text=snippet,
        stance=stance,
        extraction_method=method,
        verified_by_user=method == "manual",
        created_by=user_id,
    )
    session.add(row)
    session.flush()
    log_activity(session, project_id, user_id, "evidence_added", target_type="paper", target_id=str(paper_id))
    return row


def evidence_by_question(session: Session, project_id: int) -> list[dict[str, Any]]:
    questions = list(
        session.scalars(
            select(ResearchQuestion)
            .where(ResearchQuestion.project_id == project_id, ResearchQuestion.archived.is_(False))
            .order_by(ResearchQuestion.position, ResearchQuestion.id)
        ).all()
    )
    rows = list(session.scalars(select(EvidenceSource).where(EvidenceSource.project_id == project_id)).all())
    grouped: dict[int | None, dict[str, list]] = defaultdict(
        lambda: {"supporting": [], "conflicting": [], "inconclusive": []}
    )
    for row in rows:
        paper = session.get(Paper, row.paper_id)
        grouped[row.research_question_id][row.stance].append({"evidence": row, "paper": paper})
    unassigned = grouped.get(None, {"supporting": [], "conflicting": [], "inconclusive": []})
    out = []
    for question in questions:
        bucket = grouped.get(question.id, {"supporting": [], "conflicting": [], "inconclusive": []})
        out.append({"question": question, **bucket})
    if any(unassigned.values()):
        out.append({"question": None, **unassigned})
    return out


def add_theme(session: Session, project_id: int, name: str, *, suggested: bool = False) -> Theme:
    clean = (name or "").strip()
    if not clean:
        raise ValueError("Theme name is required.")
    slug = "".join(ch if ch.isalnum() else "-" for ch in clean.lower()).strip("-")[:80]
    existing = session.scalar(select(Theme).where(Theme.project_id == project_id, Theme.slug == slug))
    if existing:
        return existing
    theme = Theme(
        project_id=project_id,
        name=clean,
        slug=slug,
        suggested=suggested,
        reviewed=not suggested,
    )
    session.add(theme)
    return theme


def assign_theme(session: Session, theme_id: int, evidence_id: int) -> EvidenceTheme:
    existing = session.scalar(
        select(EvidenceTheme).where(EvidenceTheme.theme_id == theme_id, EvidenceTheme.evidence_id == evidence_id)
    )
    if existing:
        return existing
    row = EvidenceTheme(theme_id=theme_id, evidence_id=evidence_id)
    session.add(row)
    return row
