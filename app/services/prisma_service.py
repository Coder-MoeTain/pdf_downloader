"""PRISMA-style counts derived only from stored project state."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import Download
from app.database.research_models import ProjectPaper, ResearchProject, SearchRun, SearchStrategy


def prisma_counts(session: Session, project: ResearchProject) -> dict[str, int]:
    papers = list(session.scalars(select(ProjectPaper).where(ProjectPaper.project_id == project.id)).all())
    identified_runs = int(
        session.scalar(
            select(func.coalesce(func.sum(SearchRun.result_count), 0)).where(SearchRun.project_id == project.id)
        )
        or 0
    )
    identified = identified_runs or len(papers)
    duplicates = sum(1 for row in papers if row.is_duplicate or row.decision_reason == "duplicate")
    identified_db = len(papers)
    removed_before = max(0, identified - identified_db) if identified_runs else duplicates
    if not identified_runs:
        identified = identified_db
        removed_before = duplicates
    screened_pool = [row for row in papers if not row.is_duplicate]
    # Title/abstract screened = left pending at TA does not count as screened
    ta_screened = sum(
        1
        for row in screened_pool
        if row.screening_stage != "title_abstract" or row.decision in {"include", "exclude", "maybe"}
    )
    ta_excluded = sum(
        1
        for row in screened_pool
        if row.decision == "exclude"
        and row.screening_stage in {"title_abstract", "excluded"}
        or (row.decision == "exclude" and row.screening_stage == "title_abstract")
    )
    ta_excluded = sum(
        1
        for row in screened_pool
        if row.decision == "exclude" and row.screening_stage != "full_text" and row.screening_stage != "included"
    )
    reports_sought = sum(
        1
        for row in screened_pool
        if row.screening_stage in {"full_text", "included", "excluded"} or (row.decision == "include")
    )
    paper_ids = [row.paper_id for row in screened_pool if row.screening_stage in {"full_text", "included", "excluded"}]
    retrieved = 0
    if paper_ids:
        retrieved = int(
            session.scalar(
                select(func.count(func.distinct(Download.paper_id))).where(
                    Download.paper_id.in_(paper_ids),
                    Download.status == "DOWNLOADED",
                    Download.local_path.is_not(None),
                )
            )
            or 0
        )
    not_retrieved = max(0, len(paper_ids) - retrieved)
    assessed = sum(
        1
        for row in screened_pool
        if row.screening_stage in {"included", "excluded"}
        or (row.screening_stage == "full_text" and row.decision in {"include", "exclude"})
    )
    ft_excluded = sum(
        1
        for row in screened_pool
        if row.screening_stage == "excluded" or (row.screening_stage == "full_text" and row.decision == "exclude")
    )
    included = sum(
        1
        for row in screened_pool
        if row.screening_stage == "included" or (row.decision == "include" and row.screening_stage == "full_text")
    )
    return {
        "identified": identified,
        "removed_before_screening": removed_before,
        "duplicates_removed": duplicates,
        "records_screened": ta_screened,
        "records_excluded": ta_excluded,
        "reports_sought": reports_sought,
        "reports_not_retrieved": not_retrieved,
        "reports_assessed": assessed,
        "reports_excluded": ft_excluded,
        "studies_included": included,
        "search_runs": int(
            session.scalar(select(func.count(SearchRun.id)).where(SearchRun.project_id == project.id)) or 0
        ),
        "strategies": int(
            session.scalar(select(func.count(SearchStrategy.id)).where(SearchStrategy.project_id == project.id)) or 0
        ),
    }


def prisma_svg(counts: dict[str, Any], *, title: str = "PRISMA flow") -> str:
    boxes = [
        ("Identification", f"Records identified\n(n = {counts['identified']})"),
        ("", f"Duplicates / removed before screening\n(n = {counts['duplicates_removed']})"),
        ("Screening", f"Records screened\n(n = {counts['records_screened']})"),
        ("", f"Records excluded\n(n = {counts['records_excluded']})"),
        ("Eligibility", f"Reports sought for retrieval\n(n = {counts['reports_sought']})"),
        ("", f"Reports not retrieved\n(n = {counts['reports_not_retrieved']})"),
        ("", f"Reports assessed for eligibility\n(n = {counts['reports_assessed']})"),
        ("", f"Reports excluded\n(n = {counts['reports_excluded']})"),
        ("Included", f"Studies included\n(n = {counts['studies_included']})"),
    ]
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 980" role="img" aria-label="PRISMA flow diagram">',
        "<style>text{font-family:Source Sans 3,Segoe UI,sans-serif;fill:#0f172a} .muted{fill:#475569;font-size:13px} .box{fill:#fff;stroke:#1e3a8a;stroke-width:1.5} .side{fill:#fff7ed;stroke:#b45309}</style>",
        f'<text x="24" y="36" font-size="20" font-weight="700">{_xml(title)}</text>',
        '<text class="muted" x="24" y="58">Counts are computed from stored project screening data. They are not estimates.</text>',
    ]
    y = 90
    for index, (phase, label) in enumerate(boxes):
        side = (
            index % 2 == 1
            and "excluded" in label.lower()
            or "duplicate" in label.lower()
            or "not retrieved" in label.lower()
        )
        x = 400 if side else 80
        css = "side" if side else "box"
        parts.append(f'<rect class="{css}" x="{x}" y="{y}" width="240" height="72" rx="8"/>')
        for line_i, line in enumerate(label.split("\n")):
            parts.append(f'<text x="{x + 16}" y="{y + 28 + line_i * 20}" font-size="14">{_xml(line)}</text>')
        if phase:
            parts.append(
                f'<text x="24" y="{y + 42}" font-size="12" fill="#1e3a8a" font-weight="700">{_xml(phase)}</text>'
            )
        if index < len(boxes) - 1 and not side:
            parts.append(
                f'<path d="M200 {y + 72} L200 {y + 88}" stroke="#1e3a8a" stroke-width="1.5" marker-end="url(#arr)"/>'
            )
        y += 92 if not side else 0
        if side:
            y += 8
        else:
            y += 16
    parts.append("</svg>")
    return "\n".join(parts)


def prisma_pdf(counts: dict[str, Any], *, title: str = "PRISMA flow") -> bytes:
    doc = _prisma_page(counts, title)
    data = doc.tobytes()
    doc.close()
    return data


def prisma_png(counts: dict[str, Any], *, title: str = "PRISMA flow") -> bytes:
    doc = _prisma_page(counts, title)
    pixmap = doc[0].get_pixmap(dpi=132)
    data = pixmap.tobytes("png")
    doc.close()
    return data


def _prisma_page(counts: dict[str, Any], title: str):
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((36, 36), (title or "PRISMA flow")[:90], fontsize=14, color=(15 / 255, 23 / 255, 42 / 255))
    page.insert_text(
        (36, 54),
        "Counts are computed from stored project screening data. They are not estimates.",
        fontsize=9,
        color=(71 / 255, 85 / 255, 105 / 255),
    )
    boxes = [
        (False, "Identification", f"Records identified (n = {counts.get('identified', 0)})"),
        (True, "", f"Duplicates / removed (n = {counts.get('duplicates_removed', 0)})"),
        (False, "Screening", f"Records screened (n = {counts.get('records_screened', 0)})"),
        (True, "", f"Records excluded (n = {counts.get('records_excluded', 0)})"),
        (False, "Eligibility", f"Reports sought (n = {counts.get('reports_sought', 0)})"),
        (True, "", f"Reports not retrieved (n = {counts.get('reports_not_retrieved', 0)})"),
        (False, "", f"Reports assessed (n = {counts.get('reports_assessed', 0)})"),
        (True, "", f"Reports excluded (n = {counts.get('reports_excluded', 0)})"),
        (False, "Included", f"Studies included (n = {counts.get('studies_included', 0)})"),
    ]
    y = 78
    last_main_bottom = None
    for side, phase, label in boxes:
        x = 330 if side else 70
        rect = fitz.Rect(x, y, x + 200, y + 52)
        color = (180 / 255, 83 / 255, 9 / 255) if side else (30 / 255, 58 / 255, 138 / 255)
        fill = (255 / 255, 247 / 255, 237 / 255) if side else (1, 1, 1)
        page.draw_rect(rect, color=color, fill=fill, width=1.2)
        if phase:
            page.insert_text((36, y + 28), phase, fontsize=9, color=(30 / 255, 58 / 255, 138 / 255))
        page.insert_textbox(rect + (8, 10, -8, -8), label, fontsize=10, color=(15 / 255, 23 / 255, 42 / 255))
        if side:
            if last_main_bottom is not None:
                page.draw_line((270, last_main_bottom - 26), (330, y + 26), color=color, width=1)
            y += 8
        else:
            if last_main_bottom is not None:
                page.draw_line((170, last_main_bottom), (170, y), color=(30 / 255, 58 / 255, 138 / 255), width=1)
            last_main_bottom = y + 52
            y += 68
    return doc


def _xml(value: str) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
