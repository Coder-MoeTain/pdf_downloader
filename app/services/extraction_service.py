"""Structured extraction, evidence provenance, quality assessment, and matrix rows."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Paper
from app.database.research_models import (
    VERIFICATION_STATES,
    EvidenceSource,
    ExtractedValue,
    ExtractionField,
    ExtractionSchema,
    ProjectPaper,
    QualityAnswer,
    QualityAssessment,
    QualityChecklist,
    ResearchProject,
)
from app.services.project_service import log_activity
from app.utils.time import utc_now


def project_schema(session: Session, project_id: int) -> ExtractionSchema | None:
    return session.scalar(select(ExtractionSchema).where(ExtractionSchema.project_id == project_id))


def add_custom_field(session: Session, project_id: int, *, key: str, label: str) -> ExtractionField:
    schema = project_schema(session, project_id)
    if schema is None:
        raise ValueError("Extraction schema is missing.")
    clean_key = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in (key or label).lower())[:64]
    if not clean_key:
        raise ValueError("Field key is required.")
    existing = session.scalar(
        select(ExtractionField).where(ExtractionField.schema_id == schema.id, ExtractionField.key == clean_key)
    )
    if existing:
        raise ValueError("A field with that key already exists.")
    position = (
        session.scalar(
            select(ExtractionField.position)
            .where(ExtractionField.schema_id == schema.id)
            .order_by(ExtractionField.position.desc())
        )
        or 0
    ) + 1
    field = ExtractionField(
        schema_id=schema.id, key=clean_key, label=(label or key).strip(), position=position, is_custom=True
    )
    session.add(field)
    return field


def set_extracted_value(
    session: Session,
    project: ResearchProject,
    user_id: int,
    project_paper_id: int,
    field_id: int,
    value: str,
    *,
    method: str = "manual",
    state: str = "user_edited",
    confidence: float | None = None,
    evidence_text: str = "",
    page_number: int | None = None,
    section: str = "",
    question_id: int | None = None,
) -> ExtractedValue:
    row = session.get(ProjectPaper, project_paper_id)
    if row is None or row.project_id != project.id:
        raise ValueError("Project paper not found.")
    field = session.get(ExtractionField, field_id)
    if field is None:
        raise ValueError("Extraction field not found.")
    if state not in VERIFICATION_STATES:
        state = "user_edited"
    if method == "ai_assisted" and state == "user_verified":
        state = "ai_suggested"
    current = session.scalar(
        select(ExtractedValue).where(
            ExtractedValue.project_paper_id == project_paper_id, ExtractedValue.field_id == field_id
        )
    )
    text = (value or "").strip()
    if current is None:
        current = ExtractedValue(project_paper_id=project_paper_id, field_id=field_id, created_by=user_id)
        session.add(current)
    current.value = text
    current.extraction_method = method
    current.verification_state = "ai_suggested" if method == "ai_assisted" and state == "ai_suggested" else state
    current.confidence = confidence
    current.modified_by = user_id
    current.updated_at = utc_now()
    session.flush()
    if evidence_text.strip():
        session.add(
            EvidenceSource(
                extracted_value_id=current.id,
                project_id=project.id,
                paper_id=row.paper_id,
                research_question_id=question_id,
                page_number=page_number,
                section=section,
                evidence_text=evidence_text.strip(),
                extraction_method=method,
                confidence=confidence,
                verified_by_user=state in {"user_verified", "user_edited"},
                created_by=user_id,
            )
        )
    log_activity(session, project.id, user_id, "extraction_changed", target_type="project_paper", target_id=str(row.id))
    if project.status in {"planning", "searching", "screening"}:
        project.status = "extraction"
    return current


def verify_value(session: Session, value: ExtractedValue, user_id: int, *, accepted: bool) -> ExtractedValue:
    value.verification_state = "user_verified" if accepted else "rejected"
    value.modified_by = user_id
    value.updated_at = utc_now()
    return value


def save_quality_answers(
    session: Session,
    project: ResearchProject,
    user_id: int,
    project_paper_id: int,
    answers: dict[int, str],
    *,
    notes: str = "",
) -> QualityAssessment:
    row = session.get(ProjectPaper, project_paper_id)
    if row is None or row.project_id != project.id:
        raise ValueError("Project paper not found.")
    assessment = session.scalar(
        select(QualityAssessment).where(
            QualityAssessment.project_paper_id == project_paper_id, QualityAssessment.reviewer_id == user_id
        )
    )
    if assessment is None:
        assessment = QualityAssessment(project_paper_id=project_paper_id, reviewer_id=user_id)
        session.add(assessment)
        session.flush()
    assessment.notes = notes
    assessment.updated_at = utc_now()
    for question_id, raw in answers.items():
        answer = session.scalar(
            select(QualityAnswer).where(
                QualityAnswer.assessment_id == assessment.id, QualityAnswer.question_id == int(question_id)
            )
        )
        if answer is None:
            answer = QualityAnswer(assessment_id=assessment.id, question_id=int(question_id))
            session.add(answer)
        answer.value = str(raw or "")
        if str(raw).replace(".", "", 1).isdigit():
            answer.numeric_score = float(raw)
    log_activity(
        session, project.id, user_id, "quality_assessed", target_type="project_paper", target_id=str(project_paper_id)
    )
    return assessment


def quality_checklist(session: Session, project_id: int) -> QualityChecklist | None:
    return session.scalar(select(QualityChecklist).where(QualityChecklist.project_id == project_id))


def matrix_rows(session: Session, project: ResearchProject) -> dict[str, Any]:
    schema = project_schema(session, project.id)
    fields = []
    if schema:
        fields = list(
            session.scalars(
                select(ExtractionField).where(ExtractionField.schema_id == schema.id).order_by(ExtractionField.position)
            ).all()
        )
    papers = list(
        session.scalars(
            select(ProjectPaper)
            .where(ProjectPaper.project_id == project.id, ProjectPaper.is_duplicate.is_(False))
            .order_by(ProjectPaper.id)
        ).all()
    )
    rows = []
    for item in papers:
        paper = session.get(Paper, item.paper_id)
        if paper is None:
            continue
        values = {
            row.field_id: row
            for row in session.scalars(select(ExtractedValue).where(ExtractedValue.project_paper_id == item.id)).all()
        }
        evidence = {
            row.extracted_value_id: row
            for row in session.scalars(
                select(EvidenceSource).where(
                    EvidenceSource.project_id == project.id, EvidenceSource.paper_id == paper.id
                )
            ).all()
            if row.extracted_value_id
        }
        cells = []
        for field in fields:
            extracted = values.get(field.id)
            ev = evidence.get(extracted.id) if extracted else None
            cells.append(
                {
                    "field_id": field.id,
                    "key": field.key,
                    "value": extracted.value if extracted else "",
                    "state": extracted.verification_state if extracted else "",
                    "method": extracted.extraction_method if extracted else "",
                    "page": ev.page_number if ev else None,
                    "section": ev.section if ev else "",
                    "evidence": ev.evidence_text if ev else "",
                }
            )
        rows.append(
            {
                "project_paper_id": item.id,
                "paper_id": paper.id,
                "title": paper.title,
                "year": paper.publication_year,
                "authors": ", ".join(
                    link.author.name for link in (paper.authors or []) if getattr(link, "author", None)
                )[:180],
                "decision": item.decision,
                "stage": item.screening_stage,
                "cells": cells,
            }
        )
    return {"fields": [{"id": f.id, "key": f.key, "label": f.label} for f in fields], "rows": rows}


def matrix_csv(payload: dict[str, Any]) -> str:
    output = io.StringIO()
    headers = ["Paper", "Year", "Authors", "Stage", "Decision"] + [field["label"] for field in payload["fields"]]
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in payload["rows"]:
        writer.writerow(
            [row["title"], row["year"] or "", row["authors"], row["stage"], row["decision"]]
            + [cell["value"] for cell in row["cells"]]
        )
    return output.getvalue()


def matrix_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, default=str)


def matrix_xlsx(payload: dict[str, Any]) -> bytes:
    import pandas as pd

    headers = ["Paper", "Year", "Authors", "Stage", "Decision"] + [field["label"] for field in payload["fields"]]
    rows = []
    for row in payload["rows"]:
        rows.append(
            [row["title"], row["year"] or "", row["authors"], row["stage"], row["decision"]]
            + [cell["value"] for cell in row["cells"]]
        )
    buffer = io.BytesIO()
    pd.DataFrame(rows, columns=headers).to_excel(buffer, index=False)
    return buffer.getvalue()


def latex_table(payload: dict[str, Any]) -> str:
    fields = payload["fields"][:8]
    cols = "l" + "c" * (2 + len(fields))
    lines = [r"\begin{tabular}{" + cols + r"}", r"\hline"]
    header = "Paper & Year & " + " & ".join(_tex(f["label"]) for f in fields) + r" \\"
    lines.append(header)
    lines.append(r"\hline")
    for row in payload["rows"][:40]:
        cells = {cell["field_id"]: cell["value"] for cell in row["cells"]}
        values = " & ".join(_tex((cells.get(f["id"]) or "")[:40]) for f in fields)
        lines.append(_tex(row["title"][:48]) + " & " + str(row["year"] or "") + " & " + values + r" \\")
    lines.append(r"\hline")
    lines.append(r"\end{tabular}")
    return "\n".join(lines)


def _tex(value: str) -> str:
    return str(value).replace("\\", "\\textbackslash{}").replace("&", "\\&").replace("%", "\\%").replace("_", "\\_")
