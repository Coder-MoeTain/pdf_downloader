"""Optional local PDF text extraction and full-text search."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import load_config
from app.database.connection import session_scope
from app.database.models import Download, Paper
from app.database.repository import fulltext_search, save_fulltext
from app.utils.filename import safe_join
from app.utils.logger import get_logger

logger = get_logger("app.fulltext")


def extract_pdf_text(path: Path, max_chars: int = 500_000) -> str:
    import fitz

    doc = fitz.open(path)
    try:
        parts: list[str] = []
        total = 0
        for page in doc:
            text = page.get_text("text") or ""
            parts.append(text)
            total += len(text)
            if total >= max_chars:
                break
        return "\n".join(parts)[:max_chars]
    finally:
        doc.close()


def index_pdfs() -> int:
    cfg = load_config()
    library = cfg.resolve_path(cfg.library_dir)
    count = 0
    with session_scope() as session:
        downloads = session.scalars(
            select(Download).where(Download.status == "DOWNLOADED", Download.local_path.is_not(None))
        ).all()
        for row in downloads:
            path = Path(row.local_path)
            if not path.is_absolute():
                path = (library.parent / path).resolve()
            if not path.exists():
                continue
            try:
                text = extract_pdf_text(path)
            except Exception as exc:
                logger.warning("Failed to extract text from %s: %s", path, exc)
                continue
            if text.strip():
                save_fulltext(session, row.paper_id, text)
                dest_dir = cfg.resolve_path(cfg.fulltext_dir)
                out = safe_join(dest_dir, f"{row.paper_id}.txt")
                out.write_text(text, encoding="utf-8")
                count += 1
    return count


def search_fulltext(query: str, limit: int = 25) -> list[dict]:
    with session_scope() as session:
        hits = fulltext_search(session, query, limit=limit)
        results = []
        for paper, snippet in hits:
            results.append(
                {
                    "id": paper.id,
                    "title": paper.title,
                    "year": paper.publication_year,
                    "doi": paper.doi,
                    "snippet": snippet.replace("\n", " ")[:400],
                }
            )
        return results
