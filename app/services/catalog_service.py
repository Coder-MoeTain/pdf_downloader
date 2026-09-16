"""Dataset/algorithm catalogs and reproducibility tracking from extracted values."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Paper
from app.database.research_models import (
    AlgorithmRecord,
    DatasetRecord,
    ExtractedValue,
    ExtractionField,
    ExtractionSchema,
    ProjectPaper,
    ReproducibilityRecord,
    ResearchProject,
)

_TRUE = {"1", "yes", "true", "y"}
_FALSE = {"0", "no", "false", "n"}


def normalize_catalog_name(name: str) -> str:
    return " ".join((name or "").lower().split())[:255]


def extracted_part_counts(session: Session, project: ResearchProject, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    schema = session.scalar(select(ExtractionSchema).where(ExtractionSchema.project_id == project.id))
    if schema is None:
        return []
    fields = list(
        session.scalars(
            select(ExtractionField).where(ExtractionField.schema_id == schema.id, ExtractionField.key.in_(keys))
        ).all()
    )
    if not fields:
        return []
    field_ids = [field.id for field in fields]
    values = list(session.scalars(select(ExtractedValue).where(ExtractedValue.field_id.in_(field_ids))).all())
    counts: Counter[str] = Counter()
    for row in values:
        for part in _split_parts(row.value):
            counts[part] += 1
    return [{"name": name, "count": count} for name, count in counts.most_common(40)]


def refresh_catalogs(session: Session, project: ResearchProject) -> dict[str, Any]:
    datasets = extracted_part_counts(session, project, ("datasets",))
    algorithms = extracted_part_counts(session, project, ("algorithms", "models", "methodology"))
    dataset_by_algo = _dataset_cooccurrence(session, project)
    metrics = extracted_part_counts(session, project, ("metrics", "accuracy", "precision", "recall", "f1_score", "auc"))
    _upsert_datasets(session, project.id, datasets)
    _upsert_algorithms(session, project.id, algorithms, dataset_by_algo)
    return {
        "datasets": list(session.scalars(select(DatasetRecord).where(DatasetRecord.project_id == project.id)).all()),
        "algorithms": list(
            session.scalars(select(AlgorithmRecord).where(AlgorithmRecord.project_id == project.id)).all()
        ),
        "metrics": metrics,
        "metrics_caveat": "Metric values are listed per extracted field. Do not treat catalog counts as a meta-analysis.",
    }


def catalog_overview(session: Session, project: ResearchProject) -> dict[str, Any]:
    datasets = list(
        session.scalars(
            select(DatasetRecord).where(DatasetRecord.project_id == project.id).order_by(DatasetRecord.paper_count.desc())
        ).all()
    )
    algorithms = list(
        session.scalars(
            select(AlgorithmRecord)
            .where(AlgorithmRecord.project_id == project.id)
            .order_by(AlgorithmRecord.paper_count.desc())
        ).all()
    )
    if not datasets and not algorithms:
        return refresh_catalogs(session, project)
    metrics = extracted_part_counts(session, project, ("metrics", "accuracy", "precision", "recall", "f1_score", "auc"))
    return {
        "datasets": datasets,
        "algorithms": algorithms,
        "metrics": metrics,
        "metrics_caveat": "Metric values are listed per extracted field. Do not treat catalog counts as a meta-analysis.",
    }


def reproducibility_rows(session: Session, project: ResearchProject) -> list[dict[str, Any]]:
    items = list(
        session.scalars(
            select(ProjectPaper).where(ProjectPaper.project_id == project.id, ProjectPaper.is_duplicate.is_(False))
        ).all()
    )
    rows = []
    for item in items:
        paper = session.get(Paper, item.paper_id)
        if paper is None:
            continue
        record = session.scalar(
            select(ReproducibilityRecord).where(ReproducibilityRecord.project_paper_id == item.id)
        )
        rows.append({"item": item, "paper": paper, "record": record, "score": _repro_score(record)})
    rows.sort(key=lambda row: (-row["score"], (row["paper"].title or "").lower()))
    return rows


def upsert_reproducibility(
    session: Session,
    project: ResearchProject,
    project_paper_id: int,
    *,
    code_available: str = "",
    code_url: str = "",
    dataset_available: str = "",
    dataset_url: str = "",
    environment_described: str = "",
    hyperparameters_reported: str = "",
    random_seed_reported: str = "",
    pretrained_model_available: str = "",
    license_available: str = "",
    reproduced_by_user: str = "",
    reproduction_notes: str = "",
) -> ReproducibilityRecord:
    item = session.get(ProjectPaper, project_paper_id)
    if item is None or item.project_id != project.id:
        raise ValueError("Project paper not found.")
    row = session.scalar(select(ReproducibilityRecord).where(ReproducibilityRecord.project_paper_id == item.id))
    if row is None:
        row = ReproducibilityRecord(project_paper_id=item.id)
        session.add(row)
    row.code_available = _tri(code_available)
    row.code_url = (code_url or "").strip()[:2000]
    row.dataset_available = _tri(dataset_available)
    row.dataset_url = (dataset_url or "").strip()[:2000]
    row.environment_described = _tri(environment_described)
    row.hyperparameters_reported = _tri(hyperparameters_reported)
    row.random_seed_reported = _tri(random_seed_reported)
    row.pretrained_model_available = _tri(pretrained_model_available)
    row.license_available = _tri(license_available)
    row.reproduced_by_user = _tri(reproduced_by_user) is True
    row.reproduction_notes = (reproduction_notes or "").strip()
    session.flush()
    return row


def _split_parts(value: str | None) -> list[str]:
    if not value:
        return []
    parts = []
    seen: set[str] = set()
    for raw in value.replace(";", ",").split(","):
        name = raw.strip()
        key = normalize_catalog_name(name)
        if name and key not in seen:
            seen.add(key)
            parts.append(name[:255])
    return parts


def _dataset_cooccurrence(session: Session, project: ResearchProject) -> dict[str, set[str]]:
    schema = session.scalar(select(ExtractionSchema).where(ExtractionSchema.project_id == project.id))
    if schema is None:
        return {}
    fields = {
        field.key: field
        for field in session.scalars(select(ExtractionField).where(ExtractionField.schema_id == schema.id)).all()
    }
    algo_field_ids = [fields[key].id for key in ("algorithms", "models", "methodology") if key in fields]
    dataset_field = fields.get("datasets")
    if not algo_field_ids or dataset_field is None:
        return {}
    by_paper: dict[int, dict[str, list[str]]] = defaultdict(lambda: {"algo": [], "data": []})
    for row in session.scalars(
        select(ExtractedValue).where(ExtractedValue.field_id.in_([*algo_field_ids, dataset_field.id]))
    ).all():
        bucket = "data" if row.field_id == dataset_field.id else "algo"
        by_paper[row.project_paper_id][bucket].extend(_split_parts(row.value))
    mapping: dict[str, set[str]] = defaultdict(set)
    for payload in by_paper.values():
        for algo in payload["algo"]:
            mapping[normalize_catalog_name(algo)].update(payload["data"])
    return mapping


def _upsert_datasets(session: Session, project_id: int, rows: list[dict[str, Any]]) -> None:
    existing = {
        row.normalized_name: row
        for row in session.scalars(select(DatasetRecord).where(DatasetRecord.project_id == project_id)).all()
    }
    for item in rows:
        key = normalize_catalog_name(item["name"])
        row = existing.get(key)
        if row is None:
            row = DatasetRecord(project_id=project_id, name=item["name"][:255], normalized_name=key)
            session.add(row)
            existing[key] = row
        row.paper_count = int(item["count"])
        if not row.name:
            row.name = item["name"][:255]


def _upsert_algorithms(
    session: Session, project_id: int, rows: list[dict[str, Any]], datasets_by_algo: dict[str, set[str]]
) -> None:
    existing = {
        row.normalized_name: row
        for row in session.scalars(select(AlgorithmRecord).where(AlgorithmRecord.project_id == project_id)).all()
    }
    for item in rows:
        key = normalize_catalog_name(item["name"])
        row = existing.get(key)
        if row is None:
            row = AlgorithmRecord(project_id=project_id, name=item["name"][:255], normalized_name=key)
            session.add(row)
            existing[key] = row
        row.paper_count = int(item["count"])
        linked = sorted(datasets_by_algo.get(key) or [])
        row.datasets = "; ".join(linked)[:2000]


def _tri(value: str | None) -> bool | None:
    text = (value or "").strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def _repro_score(record: ReproducibilityRecord | None) -> int:
    if record is None:
        return 0
    flags = (
        record.code_available,
        record.dataset_available,
        record.environment_described,
        record.hyperparameters_reported,
        record.random_seed_reported,
        record.pretrained_model_available,
        record.license_available,
        record.reproduced_by_user,
    )
    return sum(1 for flag in flags if flag is True)
