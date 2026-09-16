"""Project bibliometrics computed from included/identified papers only."""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Paper
from app.database.repository import split_tags
from app.database.research_models import ExtractedValue, ExtractionField, ProjectPaper, ResearchProject


def _papers(session: Session, project: ResearchProject, *, included_only: bool = False) -> list[Paper]:
    stmt = select(ProjectPaper).where(ProjectPaper.project_id == project.id, ProjectPaper.is_duplicate.is_(False))
    rows = list(session.scalars(stmt).all())
    if included_only:
        rows = [row for row in rows if row.screening_stage == "included" or row.decision == "include"]
    papers = []
    for row in rows:
        paper = session.get(Paper, row.paper_id)
        if paper is not None:
            papers.append(paper)
    return papers


def bibliometric_overview(session: Session, project: ResearchProject, *, included_only: bool = False) -> dict[str, Any]:
    papers = _papers(session, project, included_only=included_only)
    years = Counter(paper.publication_year for paper in papers if paper.publication_year)
    journals = Counter((paper.journal or paper.publisher or "Unknown").strip() for paper in papers)
    authors: Counter[str] = Counter()
    keywords: Counter[str] = Counter()
    oa = 0
    citations = []
    for paper in papers:
        if paper.open_access:
            oa += 1
        if paper.citation_count:
            citations.append(paper.citation_count)
        for link in paper.authors or []:
            if getattr(link, "author", None) and link.author.name:
                authors[link.author.name] += 1
        for tag in split_tags(paper.keywords) + split_tags(paper.research_fields):
            keywords[tag.lower()] += 1
    year_rows = [{"year": year, "count": count} for year, count in sorted(years.items())]
    return {
        "paper_count": len(papers),
        "oa_pct": round((oa / len(papers)) * 100, 1) if papers else 0,
        "citation_total": sum(citations),
        "citation_median": _median(citations),
        "years": year_rows,
        "journals": [{"name": name, "count": count} for name, count in journals.most_common(12)],
        "authors": [{"name": name, "count": count} for name, count in authors.most_common(12)],
        "keywords": [{"name": name, "count": count} for name, count in keywords.most_common(20)],
        "keyword_cooccurrence": _cooccurrence(papers, limit=12),
        "methods": _extracted_catalog(session, project, "methodology"),
        "datasets": _extracted_catalog(session, project, "datasets"),
        "algorithms": _extracted_catalog(session, project, "algorithms"),
        "included_only": included_only,
    }


def _extracted_catalog(session: Session, project: ResearchProject, key: str) -> list[dict[str, Any]]:
    from app.database.research_models import ExtractionSchema

    schema = session.scalar(select(ExtractionSchema).where(ExtractionSchema.project_id == project.id))
    if schema is None:
        return []
    field = session.scalar(
        select(ExtractionField).where(ExtractionField.schema_id == schema.id, ExtractionField.key == key)
    )
    if field is None:
        return []
    values = list(session.scalars(select(ExtractedValue).where(ExtractedValue.field_id == field.id)).all())
    counts: Counter[str] = Counter()
    for row in values:
        text = (row.value or "").strip()
        if not text:
            continue
        for part in [item.strip() for item in text.replace(";", ",").split(",") if item.strip()]:
            counts[part] += 1
    return [{"name": name, "count": count} for name, count in counts.most_common(12)]


def _cooccurrence(papers: list[Paper], *, limit: int = 12) -> list[dict[str, Any]]:
    pairs: Counter[tuple[str, str]] = Counter()
    for paper in papers:
        tags = sorted({tag.lower() for tag in split_tags(paper.keywords)})
        for i, left in enumerate(tags):
            for right in tags[i + 1 :]:
                pairs[(left, right)] += 1
    return [{"a": a, "b": b, "count": count} for (a, b), count in pairs.most_common(limit)]


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2
