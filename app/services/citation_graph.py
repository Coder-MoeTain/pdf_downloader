"""Project citation-style graph from stored bibliographic overlap, with expansion."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import Paper
from app.database.repository import related_papers_with_reasons, split_tags
from app.database.research_models import ProjectPaper, ResearchProject
from app.services.bibliometric_service import _papers

GRAPH_DISCLAIMER = (
    "Edges show shared keywords, authors, or venue in stored metadata. "
    "They are not verified citation links unless reference lists were ingested."
)


def citation_graph(
    session: Session,
    project: ResearchProject,
    *,
    expand_id: int | None = None,
    included_only: bool = True,
    limit: int = 40,
) -> dict[str, Any]:
    papers = _papers(session, project, included_only=included_only)
    if not papers and included_only:
        papers = _papers(session, project, included_only=False)
    papers = papers[:limit]
    in_project = {paper.id for paper in papers}
    if expand_id and expand_id not in in_project:
        extra = session.get(Paper, expand_id)
        if extra is not None:
            papers.append(extra)
    nodes = [_node(paper, in_project=paper.id in in_project, seed=paper.id == expand_id) for paper in papers]
    edges = _overlap_edges(papers)
    if expand_id:
        related = related_papers_with_reasons(session, expand_id, limit=8)
        known = {node["id"] for node in nodes}
        for item in related:
            paper = item["paper"]
            if paper.id in known:
                _append_edge(edges, expand_id, paper.id, item["reasons"][0] if item["reasons"] else "Related")
                continue
            nodes.append(_node(paper, in_project=False, seed=False))
            known.add(paper.id)
            _append_edge(edges, expand_id, paper.id, item["reasons"][0] if item["reasons"] else "Related")
    return {
        "disclaimer": GRAPH_DISCLAIMER,
        "included_only": included_only,
        "expand_id": expand_id,
        "nodes": nodes,
        "edges": edges,
    }


def project_paper_ids(session: Session, project_id: int) -> set[int]:
    rows = session.scalars(select_project_paper_ids(project_id)).all()
    return {int(row) for row in rows}


def select_project_paper_ids(project_id: int):
    from sqlalchemy import select

    return select(ProjectPaper.paper_id).where(ProjectPaper.project_id == project_id)


def _node(paper: Paper, *, in_project: bool, seed: bool) -> dict[str, Any]:
    return {
        "id": paper.id,
        "title": paper.title,
        "year": paper.publication_year,
        "journal": (paper.journal or paper.conference or "")[:80],
        "citations": paper.citation_count or 0,
        "in_project": in_project,
        "seed": seed,
    }


def _paper_signature(paper: Paper) -> dict[str, set[str]]:
    authors = {
        link.author.name.strip().lower()
        for link in (paper.authors or [])
        if getattr(link, "author", None) and (link.author.name or "").strip()
    }
    return {
        "keywords": {tag.lower() for tag in split_tags(paper.keywords) + split_tags(paper.research_fields)},
        "authors": authors,
        "journal": {(paper.journal or "").strip().lower()} if (paper.journal or "").strip() else set(),
    }


def _overlap_edges(papers: list[Paper]) -> list[dict[str, Any]]:
    signatures = {paper.id: _paper_signature(paper) for paper in papers}
    grouped: dict[tuple[int, int], list[str]] = defaultdict(list)
    for i, left in enumerate(papers):
        left_sig = signatures[left.id]
        for right in papers[i + 1 :]:
            right_sig = signatures[right.id]
            shared_kw = left_sig["keywords"] & right_sig["keywords"]
            if shared_kw:
                grouped[(left.id, right.id)].append("Similar keywords")
            if left_sig["authors"] & right_sig["authors"]:
                grouped[(left.id, right.id)].append("Shared author")
            if left_sig["journal"] & right_sig["journal"]:
                grouped[(left.id, right.id)].append("Same journal")
    edges = []
    for (source, target), reasons in grouped.items():
        edges.append({"source": source, "target": target, "reason": reasons[0], "reasons": reasons, "weight": len(reasons)})
    return edges


def _append_edge(edges: list[dict[str, Any]], source: int, target: int, reason: str) -> None:
    pair = {source, target}
    for edge in edges:
        if {edge["source"], edge["target"]} == pair:
            if reason not in edge["reasons"]:
                edge["reasons"].append(reason)
                edge["weight"] = len(edge["reasons"])
            return
    edges.append({"source": source, "target": target, "reason": reason, "reasons": [reason], "weight": 1})
