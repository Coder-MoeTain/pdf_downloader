"""Clear the local paper library, search history, and stored PDF files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select

from app.config import AppConfig, get_runtime_config
from app.database.connection import session_scope
from app.database.models import (
    Author,
    CrawlJob,
    Download,
    LmsExport,
    Paper,
    PaperAuthor,
    PaperFulltext,
    SearchJob,
    SearchQuery,
    SearchResult,
)
from app.services.download_service import safe_library_pdf
from app.services.progress import crawl_job_registry, download_tracker, job_registry, tracker
from app.utils.logger import get_logger

logger = get_logger("app.library_reset")


@dataclass
class LibraryResetStats:
    search_jobs: int = 0
    search_queries: int = 0
    crawl_jobs: int = 0
    papers: int = 0
    pdf_files_removed: int = 0


def reset_library_repository(config: AppConfig | None = None) -> LibraryResetStats:
    """Delete all searches, papers, downloads, and PDF files in the library."""
    cfg = config or get_runtime_config()
    stats = LibraryResetStats()

    with session_scope() as session:
        stats.search_jobs = session.scalar(select(func.count()).select_from(SearchJob)) or 0
        stats.search_queries = session.scalar(select(func.count()).select_from(SearchQuery)) or 0
        stats.crawl_jobs = session.scalar(select(func.count()).select_from(CrawlJob)) or 0
        stats.papers = session.scalar(select(func.count()).select_from(Paper)) or 0
        local_paths = list(
            session.scalars(select(Download.local_path).where(Download.local_path.isnot(None))).all()
        )

        session.execute(delete(SearchJob))
        session.execute(delete(SearchResult))
        session.execute(delete(SearchQuery))
        session.execute(delete(CrawlJob))
        session.execute(delete(LmsExport))
        session.execute(delete(Download))
        session.execute(delete(PaperFulltext))
        session.execute(delete(PaperAuthor))
        session.execute(delete(Paper))
        session.execute(delete(Author))

    library_root = cfg.resolve_path(cfg.library_dir)
    stats.pdf_files_removed = _remove_library_pdfs(library_root, local_paths)

    job_registry.clear_all()
    crawl_job_registry.clear_all()
    download_tracker.reset()
    tracker.reset()

    logger.info(
        "Library reset: %s papers, %s search jobs, %s PDF files removed",
        stats.papers,
        stats.search_jobs,
        stats.pdf_files_removed,
    )
    return stats


def _remove_library_pdfs(library_root: Path, known_paths: list[str]) -> int:
    removed = 0
    seen: set[Path] = set()
    for path_value in known_paths:
        dest = safe_library_pdf(path_value, library_root)
        if dest is None or dest in seen:
            continue
        seen.add(dest)
        if dest.is_file():
            dest.unlink(missing_ok=True)
            removed += 1
    if library_root.is_dir():
        for pdf in library_root.rglob("*.pdf"):
            if pdf in seen:
                continue
            if pdf.is_file():
                pdf.unlink(missing_ok=True)
                removed += 1
    return removed
