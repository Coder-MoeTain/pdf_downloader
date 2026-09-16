"""Private PDF annotations. Never mutate the original file."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.research_models import PaperAnnotation


def list_annotations(
    session: Session, user_id: int, paper_id: int, *, project_id: int | None = None
) -> list[PaperAnnotation]:
    stmt = select(PaperAnnotation).where(PaperAnnotation.user_id == user_id, PaperAnnotation.paper_id == paper_id)
    if project_id is not None:
        stmt = stmt.where((PaperAnnotation.project_id == project_id) | (PaperAnnotation.project_id.is_(None)))
    return list(session.scalars(stmt.order_by(PaperAnnotation.page_number, PaperAnnotation.id)).all())


def add_annotation(
    session: Session,
    user_id: int,
    paper_id: int,
    *,
    project_id: int | None,
    page_number: int,
    annotation_type: str,
    selected_text: str = "",
    comment: str = "",
    color_key: str = "yellow",
    position_json: str = "{}",
) -> PaperAnnotation:
    kind = annotation_type if annotation_type in {"highlight", "note", "bookmark"} else "note"
    row = PaperAnnotation(
        user_id=user_id,
        paper_id=paper_id,
        project_id=project_id,
        page_number=max(1, int(page_number or 1)),
        annotation_type=kind,
        selected_text=(selected_text or "").strip(),
        comment=(comment or "").strip(),
        color_key=(color_key or "yellow")[:16],
        position_json=position_json or "{}",
    )
    session.add(row)
    session.flush()
    return row


def delete_annotation(session: Session, user_id: int, annotation_id: int) -> bool:
    row = session.get(PaperAnnotation, annotation_id)
    if row is None or row.user_id != user_id:
        return False
    session.delete(row)
    return True
