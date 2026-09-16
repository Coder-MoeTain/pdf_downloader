"""Hybrid retrieval over FTS5 + optional embeddings. Answers require page evidence."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Paper
from app.database.research_models import DocumentChunk
from app.services.research_llm import llm_provider
from app.utils.logger import get_logger

logger = get_logger("app.research_rag")

_NO_EVIDENCE = "I could not find sufficient evidence in this paper."
_CHUNK = 1100


def extract_pdf_pages(path, max_pages: int = 80) -> list[tuple[int, str]]:
    import fitz

    doc = fitz.open(path)
    pages = []
    try:
        for index, page in enumerate(doc, start=1):
            if index > max_pages:
                break
            pages.append((index, page.get_text("text") or ""))
    finally:
        doc.close()
    return pages


def chunk_pages(pages: list[tuple[int, str]]) -> list[dict[str, Any]]:
    chunks = []
    index = 0
    for page_number, raw in pages:
        text_value = re.sub(r"\s+", " ", raw).strip()
        if not text_value:
            continue
        for start in range(0, len(text_value), _CHUNK):
            piece = text_value[start : start + _CHUNK].strip()
            if len(piece) < 40:
                continue
            chunks.append(
                {
                    "page_number": page_number,
                    "section": "",
                    "chunk_index": index,
                    "content": piece,
                    "content_hash": hashlib.sha256(piece.encode("utf-8")).hexdigest(),
                }
            )
            index += 1
    return chunks


def upsert_chunks(session: Session, paper_id: int, chunks: list[dict[str, Any]]) -> int:
    existing = {
        row.content_hash: row
        for row in session.scalars(select(DocumentChunk).where(DocumentChunk.paper_id == paper_id)).all()
    }
    written = 0
    for item in chunks:
        row = existing.get(item["content_hash"])
        if row is None:
            row = DocumentChunk(paper_id=paper_id, **item)
            session.add(row)
            written += 1
        else:
            row.page_number = item["page_number"]
            row.chunk_index = item["chunk_index"]
    return written


def _embed(texts: list[str]) -> list[list[float]] | None:
    from app.config import get_runtime_config

    cfg = get_runtime_config()
    if not cfg.ranking.semantic_enabled:
        return None
    try:
        from sentence_transformers import SentenceTransformer
    except Exception:
        return None
    model_name = cfg.ranking.semantic_model or "all-MiniLM-L6-v2"
    model = SentenceTransformer(model_name)
    vectors = model.encode(texts, normalize_embeddings=True)
    return [list(map(float, row)) for row in vectors]


def index_paper_pdf(session: Session, paper_id: int, path) -> int:
    pages = extract_pdf_pages(path)
    chunks = chunk_pages(pages)
    written = upsert_chunks(session, paper_id, chunks)
    rows = list(session.scalars(select(DocumentChunk).where(DocumentChunk.paper_id == paper_id)).all())
    pending = [row for row in rows if not row.embedding_json]
    if pending:
        vectors = _embed([row.content for row in pending])
        if vectors:
            from app.config import get_runtime_config

            model_name = get_runtime_config().ranking.semantic_model
            for row, vector in zip(pending, vectors, strict=False):
                row.embedding_json = json.dumps(vector)
                row.embedding_model = model_name
    return written


def _fts_chunks(session: Session, query: str, paper_ids: list[int] | None, limit: int) -> list[DocumentChunk]:
    if paper_ids == []:
        return []
    stmt = select(DocumentChunk).where(DocumentChunk.content.ilike(f"%{query.strip()[:80]}%"))
    if paper_ids:
        stmt = stmt.where(DocumentChunk.paper_id.in_(paper_ids))
    return list(session.scalars(stmt.limit(limit)).all())


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))


def retrieve(
    session: Session, question: str, *, paper_ids: list[int] | None = None, limit: int = 8
) -> list[dict[str, Any]]:
    q = (question or "").strip()
    if not q:
        return []
    lexical = _fts_chunks(session, q, paper_ids, limit * 2)
    scored: list[tuple[float, DocumentChunk]] = [(1.0, row) for row in lexical]
    vectors = _embed([q])
    if vectors:
        query_vec = vectors[0]
        stmt = select(DocumentChunk).where(DocumentChunk.embedding_json != "")
        if paper_ids:
            stmt = stmt.where(DocumentChunk.paper_id.in_(paper_ids))
        for row in session.scalars(stmt.limit(200)).all():
            try:
                vec = json.loads(row.embedding_json)
            except Exception:
                continue
            scored.append((_cosine(query_vec, vec), row))
    scored.sort(key=lambda item: item[0], reverse=True)
    seen: set[int] = set()
    out = []
    for score, row in scored:
        if row.id in seen:
            continue
        seen.add(row.id)
        paper = session.get(Paper, row.paper_id)
        out.append(
            {
                "chunk": row,
                "paper": paper,
                "score": score,
                "citation": f"[{(paper.title if paper else 'Paper')[:48]}, p.{row.page_number}]",
            }
        )
        if len(out) >= limit:
            break
    return out


def answer_from_evidence(question: str, hits: list[dict[str, Any]], *, allow_remote: bool = False) -> dict[str, Any]:
    if not hits:
        return {
            "answer": _NO_EVIDENCE,
            "supported": False,
            "interpretation": False,
            "citations": [],
        }
    evidence_lines = []
    citations = []
    for hit in hits:
        chunk = hit["chunk"]
        paper = hit["paper"]
        title = paper.title if paper else f"Paper {chunk.paper_id}"
        evidence_lines.append(f"{title} p.{chunk.page_number}: {chunk.content[:500]}")
        citations.append(
            {
                "paper_id": chunk.paper_id,
                "title": title,
                "page": chunk.page_number,
                "excerpt": chunk.content[:280],
                "label": f"[Paper {chunk.paper_id}, p.{chunk.page_number}]",
            }
        )
    prompt = (
        f"Question: {question}\n\nEvidence excerpts:\n"
        + "\n".join(evidence_lines)
        + "\n\nWrite a short answer using only this evidence. Cite as [Paper id, p.N]. "
        "If the evidence is not enough, say you could not find sufficient evidence."
    )
    generated = llm_provider.complete(prompt, allow_remote=allow_remote)
    if generated:
        return {
            "answer": generated,
            "supported": True,
            "interpretation": True,
            "citations": citations,
        }
    # Extractive fallback: never invent. Quote the best matching excerpt.
    best = hits[0]
    chunk = best["chunk"]
    paper = best["paper"]
    label = f"[Paper {chunk.paper_id}, p.{chunk.page_number}]"
    answer = f"{chunk.content[:400].strip()} {label}"
    return {
        "answer": answer,
        "supported": True,
        "interpretation": False,
        "citations": citations,
    }


def related_with_reasons(session: Session, paper: Paper, *, limit: int = 8) -> list[dict[str, Any]]:
    from app.database.repository import related_papers

    rows = related_papers(session, paper.id, limit=limit)
    out = []
    source_kw = {part.strip().lower() for part in (paper.keywords or "").split(";") if part.strip()}
    for row in rows:
        reasons = []
        other_kw = {part.strip().lower() for part in (row.keywords or "").split(";") if part.strip()}
        if source_kw & other_kw:
            reasons.append("Similar topic")
        if paper.journal and paper.journal == row.journal:
            reasons.append("Same venue")
        if paper.normalized_title and row.normalized_title:
            shared = set(paper.normalized_title.split()) & set(row.normalized_title.split())
            if len([w for w in shared if len(w) > 4]) >= 2:
                reasons.append("Similar abstract / title")
        if not reasons:
            reasons.append("Shared keywords or venue")
        out.append({"paper": row, "reasons": reasons})
    return out
