"""Evidence-based gap clustering from extracted limitations and future work."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Paper
from app.database.research_models import (
    ExtractedValue,
    ExtractionField,
    ExtractionSchema,
    ProjectPaper,
    ResearchProject,
)

_STOP = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "are",
    "was",
    "were",
    "have",
    "has",
    "not",
    "but",
    "our",
    "their",
}


def gap_clusters(session: Session, project: ResearchProject) -> list[dict[str, Any]]:
    schema = session.scalar(select(ExtractionSchema).where(ExtractionSchema.project_id == project.id))
    if schema is None:
        return []
    fields = {
        row.key: row
        for row in session.scalars(select(ExtractionField).where(ExtractionField.schema_id == schema.id)).all()
        if row.key in {"limitations", "future_work"}
    }
    if not fields:
        return []
    papers = list(
        session.scalars(
            select(ProjectPaper).where(ProjectPaper.project_id == project.id, ProjectPaper.is_duplicate.is_(False))
        ).all()
    )
    statements: list[dict[str, Any]] = []
    for item in papers:
        paper = session.get(Paper, item.paper_id)
        if paper is None:
            continue
        for key, field in fields.items():
            value = session.scalar(
                select(ExtractedValue).where(
                    ExtractedValue.project_paper_id == item.id, ExtractedValue.field_id == field.id
                )
            )
            text = (value.value if value else "") or ""
            for sentence in _split(text):
                statements.append({"text": sentence, "paper": paper, "kind": key, "project_paper_id": item.id})
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in statements:
        token = _head_tokens(item["text"])
        if not token:
            continue
        clusters[token].append(item)
    included = max(1, len(papers))
    out = []
    for label, items in sorted(clusters.items(), key=lambda pair: -len(pair[1])):
        if len(items) < 2:
            continue
        papers_n = len({row["paper"].id for row in items})
        out.append(
            {
                "label": label.replace("_", " ").title(),
                "count": papers_n,
                "total": included,
                "share": f"{papers_n} / {included} papers",
                "caveat": "Potential research gap supported by recurring limitations.",
                "items": items[:8],
            }
        )
    return out[:12]


def _split(text: str) -> list[str]:
    parts = re.split(r"[.\n;]+", text or "")
    return [part.strip() for part in parts if len(part.strip()) > 24]


def _head_tokens(text: str) -> str:
    words = [word.lower() for word in re.findall(r"[A-Za-z][A-Za-z\-]+", text) if word.lower() not in _STOP]
    return "_".join(words[:4])
