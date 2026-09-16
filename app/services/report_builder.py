"""Reproducible project snapshots and research-review reports from stored data only."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Paper
from app.database.research_models import (
    EligibilityCriterion,
    ProjectPaper,
    ResearchProject,
    ResearchQuestion,
    SearchStrategy,
)
from app.services.bibliometric_service import bibliometric_overview
from app.services.citation import paper_citations
from app.services.extraction_service import matrix_csv, matrix_rows
from app.services.gap_analysis import gap_clusters
from app.services.prisma_service import prisma_counts
from app.services.project_service import overview_kpis, review_type_label
from app.utils.time import utc_now


def snapshot_payload(session: Session, project: ResearchProject) -> dict[str, Any]:
    questions = [
        {"code": row.code, "question": row.question, "description": row.description}
        for row in session.scalars(
            select(ResearchQuestion)
            .where(ResearchQuestion.project_id == project.id)
            .order_by(ResearchQuestion.position)
        ).all()
    ]
    strategies = [
        {
            "name": row.name,
            "query": row.query,
            "year_from": row.year_from,
            "year_to": row.year_to,
            "open_access_only": row.open_access_only,
            "providers": row.provider_config_json,
            "last_result_count": row.last_result_count,
        }
        for row in session.scalars(select(SearchStrategy).where(SearchStrategy.project_id == project.id)).all()
    ]
    criteria = [
        {"type": row.type, "code": row.code, "description": row.description}
        for row in session.scalars(
            select(EligibilityCriterion).where(EligibilityCriterion.project_id == project.id)
        ).all()
    ]
    papers = []
    bibliography = []
    for item in session.scalars(select(ProjectPaper).where(ProjectPaper.project_id == project.id)).all():
        paper = session.get(Paper, item.paper_id)
        if paper is None:
            continue
        papers.append(
            {
                "paper_id": paper.id,
                "title": paper.title,
                "year": paper.publication_year,
                "doi": paper.doi,
                "stage": item.screening_stage,
                "decision": item.decision,
                "reason": item.decision_reason,
                "duplicate": item.is_duplicate,
            }
        )
        bibliography.append(paper_citations(paper))
    return {
        "project": {
            "id": project.id,
            "title": project.title,
            "slug": project.slug,
            "description": project.description,
            "review_type": project.review_type,
            "status": project.status,
            "exported_at": utc_now().isoformat(),
        },
        "kpis": overview_kpis(session, project),
        "questions": questions,
        "strategies": strategies,
        "criteria": criteria,
        "prisma": prisma_counts(session, project),
        "papers": papers,
        "matrix": matrix_rows(session, project),
        "bibliometrics": bibliometric_overview(session, project, included_only=True),
        "gaps": [
            {"label": row["label"], "share": row["share"], "caveat": row["caveat"]}
            for row in gap_clusters(session, project)
        ],
        "bibliography": bibliography,
    }


def snapshot_zip(payload: dict[str, Any]) -> bytes:
    from app.services.prisma_service import prisma_svg

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.json", json.dumps(payload, indent=2, default=str))
        archive.writestr("matrix.csv", matrix_csv(payload.get("matrix") or {"fields": [], "rows": []}))
        archive.writestr("report.md", markdown_report(payload))
        archive.writestr("report.html", html_report(payload))
        archive.writestr("report.tex", latex_report(payload))
        archive.writestr("report.docx", docx_report(payload))
        archive.writestr("prisma.svg", prisma_svg(payload.get("prisma") or {}, title=payload["project"]["title"]))
    return buffer.getvalue()


def markdown_report(payload: dict[str, Any]) -> str:
    project = payload["project"]
    prisma = payload["prisma"]
    lines = [
        f"# {project['title']}",
        "",
        f"Review type: {review_type_label(project.get('review_type') or '')}",
        "",
        "Generated from stored project data. This is a research workspace export, not a peer-reviewed conclusion.",
        "",
        "## 1. Project overview",
        project.get("description") or "_No description._",
        "",
        "## 2. Research questions",
    ]
    for row in payload.get("questions") or []:
        lines.append(f"- **{row['code']}.** {row['question']}")
    lines += ["", "## 3–5. Search method and eligibility", ""]
    for row in payload.get("strategies") or []:
        lines.append(f"- {row['name']}: `{row['query']}` (results last run: {row['last_result_count']})")
    for row in payload.get("criteria") or []:
        lines.append(f"- {row['type']} {row['code']}: {row['description']}")
    lines += [
        "",
        "## 6. PRISMA statistics",
        f"- Identified: {prisma['identified']}",
        f"- Duplicates removed: {prisma['duplicates_removed']}",
        f"- Screened: {prisma['records_screened']}",
        f"- Excluded: {prisma['records_excluded']}",
        f"- Included: {prisma['studies_included']}",
        "",
        "## 7. Included studies",
    ]
    included = [
        row for row in payload.get("papers") or [] if row.get("stage") == "included" or row.get("decision") == "include"
    ]
    for row in included:
        doi = f" doi:{row['doi']}" if row.get("doi") else ""
        lines.append(f"- {row['title']} ({row.get('year') or 'n.d.'}){doi}")
    lines += ["", "## 16. Potential research gaps", ""]
    for row in payload.get("gaps") or []:
        lines.append(f"- {row['label']} ({row['share']}). {row['caveat']}")
    if not payload.get("gaps"):
        lines.append("_No recurring limitation clusters yet. Extract limitations to populate this section._")
    lines += ["", "## 17. References", ""]
    for cite in payload.get("bibliography") or []:
        lines.append(cite.get("apa") or cite.get("ieee") or "")
    return "\n".join(lines)


def html_report(payload: dict[str, Any]) -> str:
    title = _xml(payload["project"]["title"])
    blocks: list[str] = ["<article>"]
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            blocks.append("</ul>")
            in_list = False

    for line in markdown_report(payload).splitlines():
        if line.startswith("# "):
            close_list()
            blocks.append(f"<h1>{_xml(line[2:])}</h1>")
        elif line.startswith("## "):
            close_list()
            blocks.append(f"<h2>{_xml(line[3:])}</h2>")
        elif line.startswith("- "):
            if not in_list:
                blocks.append("<ul>")
                in_list = True
            blocks.append(f"<li>{_xml(line[2:])}</li>")
        elif not line.strip():
            close_list()
        else:
            close_list()
            blocks.append(f"<p>{_xml(line)}</p>")
    close_list()
    blocks.append("</article>")
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{title}</title>"
        "<style>body{font-family:Georgia,serif;max-width:720px;margin:2rem auto;color:#0f172a;line-height:1.5}"
        "h1{font-size:1.6rem}h2{font-size:1.15rem;margin-top:1.4rem}ul{padding-left:1.2rem}</style>"
        f"</head><body>{''.join(blocks)}</body></html>"
    )


def latex_report(payload: dict[str, Any]) -> str:
    project = payload["project"]
    prisma = payload.get("prisma") or {}
    lines = [
        r"\documentclass[11pt]{article}",
        r"\usepackage[margin=1in]{geometry}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage[utf8]{inputenc}",
        rf"\title{{{_tex(project['title'])}}}",
        r"\author{Cyber Scholar export}",
        rf"\date{{{_tex(str(project.get('exported_at') or ''))}}}",
        r"\begin{document}",
        r"\maketitle",
        r"Generated from stored project data. This is a research workspace export, not a peer-reviewed conclusion.",
        r"\section{PRISMA statistics}",
        r"\begin{itemize}",
        rf"\item Identified: {int(prisma.get('identified') or 0)}",
        rf"\item Duplicates removed: {int(prisma.get('duplicates_removed') or 0)}",
        rf"\item Screened: {int(prisma.get('records_screened') or 0)}",
        rf"\item Included: {int(prisma.get('studies_included') or 0)}",
        r"\end{itemize}",
        r"\section{Research questions}",
        r"\begin{itemize}",
    ]
    questions = payload.get("questions") or []
    if not questions:
        lines.append(r"\item None recorded.")
    for row in questions:
        lines.append(rf"\item {_tex(row.get('code') or '')}: {_tex(row.get('question') or '')}")
    lines += [r"\end{itemize}", r"\section{Included studies}", r"\begin{itemize}"]
    included = [
        row for row in payload.get("papers") or [] if row.get("stage") == "included" or row.get("decision") == "include"
    ]
    if not included:
        lines.append(r"\item No included studies yet.")
    for row in included:
        doi = f" doi:{row['doi']}" if row.get("doi") else ""
        lines.append(rf"\item {_tex(row.get('title') or '')} ({_tex(str(row.get('year') or 'n.d.'))}){_tex(doi)}")
    lines += [r"\end{itemize}", r"\section{References}", r"\begin{itemize}"]
    cites = payload.get("bibliography") or []
    if not cites:
        lines.append(r"\item No bibliography yet.")
    for cite in cites:
        lines.append(rf"\item {_tex(cite.get('apa') or cite.get('ieee') or '')}")
    lines += [r"\end{itemize}", r"\end{document}", ""]
    return "\n".join(lines)


def docx_report(payload: dict[str, Any]) -> bytes:
    paragraphs: list[tuple[str, str]] = []
    for line in markdown_report(payload).splitlines():
        if line.startswith("# "):
            paragraphs.append(("title", line[2:]))
        elif line.startswith("## "):
            paragraphs.append(("heading", line[3:]))
        elif line.startswith("- "):
            paragraphs.append(("bullet", line[2:]))
        elif line.strip():
            paragraphs.append(("body", line))
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">',
        "<w:body>",
    ]
    for kind, text in paragraphs:
        style = {"title": "Title", "heading": "Heading1", "bullet": "ListParagraph"}.get(kind, "Normal")
        parts.append(
            "<w:p>"
            f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
            f'<w:r><w:t xml:space="preserve">{_xml(text)}</w:t></w:r>'
            "</w:p>"
        )
    parts.append("</w:body></w:document>")
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", "".join(parts))
    return buffer.getvalue()


def _xml(value: str) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _tex(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\textbackslash{}")
        .replace("&", "\\&")
        .replace("%", "\\%")
        .replace("$", "\\$")
        .replace("#", "\\#")
        .replace("_", "\\_")
        .replace("{", "\\{")
        .replace("}", "\\}")
    )
