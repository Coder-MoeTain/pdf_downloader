"""Remove download records that claim a PDF is saved when the file is missing on disk."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.config import AppConfig, get_runtime_config
from app.database.connection import session_scope
from app.database.models import Download, LmsExport, Paper, PaperAuthor, PaperFulltext, SearchResult
from app.database.repository import downloadable_clause, invalidate_library_facets_cache
from app.models.paper import PaperStatus
from app.utils.logger import get_logger

logger = get_logger("app.missing_pdf_cleanup")

CLAIMED_STATUSES = frozenset(
    {
        PaperStatus.DOWNLOADED.value,
        PaperStatus.DUPLICATE.value,
    }
)
_DELETE_CHUNK = 400


@dataclass
class MissingPdfCleanupStats:
    downloads_cleared: int = 0
    papers_updated: int = 0


@dataclass
class DeleteWithoutLocalPdfStats:
    papers_deleted: int = 0


def resolve_claimed_pdf_path(path_value: str | None, library_root: Path) -> Path | None:
    """Resolve a stored path under the library root without requiring the file to exist."""
    if not path_value or not str(path_value).strip():
        return None
    root = library_root.resolve()
    path = Path(path_value)
    if not path.is_absolute():
        path = (root / path).resolve()
    else:
        path = path.resolve()
    if not str(path).startswith(str(root)):
        return None
    return path


def claimed_pdf_exists(path: Path | None, *, min_size: int) -> bool:
    """True when path is a readable PDF of at least min_size bytes."""
    if path is None:
        return False
    try:
        if not path.is_file() or path.stat().st_size < min_size:
            return False
        with path.open("rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def _restore_paper_status(paper: Paper) -> None:
    if paper.status not in CLAIMED_STATUSES:
        return
    if paper.pdf_url or paper.open_access:
        paper.status = PaperStatus.OA_AVAILABLE.value
    else:
        paper.status = PaperStatus.FOUND.value


def cleanup_missing_pdf_records(config: AppConfig | None = None) -> MissingPdfCleanupStats:
    """Clear download rows whose local PDF file is missing; keep paper metadata."""
    cfg = config or get_runtime_config()
    library_root = cfg.resolve_path(cfg.library_dir)
    min_size = int(cfg.min_pdf_size_bytes)
    stats = MissingPdfCleanupStats()

    with session_scope() as session:
        papers = list(
            session.scalars(
                select(Paper)
                .options(selectinload(Paper.downloads))
                .where(
                    (Paper.status.in_(tuple(CLAIMED_STATUSES)))
                    | (Paper.id.in_(select(Download.paper_id).where(Download.status.in_(tuple(CLAIMED_STATUSES)))))
                )
            ).unique().all()
        )
        orphan_download_ids: list[int] = []
        papers_to_fix: list[Paper] = []

        for paper in papers:
            claimed_rows = [row for row in (paper.downloads or []) if row.status in CLAIMED_STATUSES]
            missing_rows = [
                row
                for row in claimed_rows
                if not claimed_pdf_exists(
                    resolve_claimed_pdf_path(row.local_path, library_root),
                    min_size=min_size,
                )
            ]
            valid_rows = [row for row in claimed_rows if row not in missing_rows]
            orphan_download_ids.extend(row.id for row in missing_rows)
            if paper.status in CLAIMED_STATUSES and not valid_rows:
                papers_to_fix.append(paper)

        if orphan_download_ids:
            session.execute(delete(Download).where(Download.id.in_(orphan_download_ids)))
            stats.downloads_cleared = len(set(orphan_download_ids))

        if papers_to_fix:
            paper_ids = [paper.id for paper in papers_to_fix]
            session.execute(delete(PaperFulltext).where(PaperFulltext.paper_id.in_(paper_ids)))
            for paper in papers_to_fix:
                _restore_paper_status(paper)
            stats.papers_updated = len(papers_to_fix)

    logger.info(
        "Missing PDF cleanup: cleared %s download rows, updated %s papers",
        stats.downloads_cleared,
        stats.papers_updated,
    )
    return stats


def _paper_has_local_pdf(paper: Paper, library_root: Path, *, min_size: int) -> bool:
    claimed_rows = [
        row
        for row in (paper.downloads or [])
        if row.status in CLAIMED_STATUSES and row.local_path
    ]
    return any(
        claimed_pdf_exists(
            resolve_claimed_pdf_path(row.local_path, library_root),
            min_size=min_size,
        )
        for row in claimed_rows
    )


def delete_papers_without_local_pdf(config: AppConfig | None = None) -> DeleteWithoutLocalPdfStats:
    """Delete paper records that do not have a real PDF file on this server."""
    cfg = config or get_runtime_config()
    library_root = cfg.resolve_path(cfg.library_dir)
    min_size = int(cfg.min_pdf_size_bytes)
    stats = DeleteWithoutLocalPdfStats()

    with session_scope() as session:
        no_claim_ids = list(session.scalars(select(Paper.id).where(~downloadable_clause())).all())
        claimed_papers = list(
            session.scalars(
                select(Paper)
                .options(selectinload(Paper.downloads))
                .where(downloadable_clause())
            )
            .unique()
            .all()
        )
        ghost_ids = [
            paper.id
            for paper in claimed_papers
            if not _paper_has_local_pdf(paper, library_root, min_size=min_size)
        ]
        delete_ids = sorted(set(no_claim_ids) | set(ghost_ids))
        if not delete_ids:
            return stats

        for start in range(0, len(delete_ids), _DELETE_CHUNK):
            chunk = delete_ids[start : start + _DELETE_CHUNK]
            session.execute(delete(SearchResult).where(SearchResult.paper_id.in_(chunk)))
            session.execute(delete(PaperFulltext).where(PaperFulltext.paper_id.in_(chunk)))
            session.execute(delete(LmsExport).where(LmsExport.paper_id.in_(chunk)))
            session.execute(delete(Download).where(Download.paper_id.in_(chunk)))
            session.execute(delete(PaperAuthor).where(PaperAuthor.paper_id.in_(chunk)))
            session.execute(delete(Paper).where(Paper.id.in_(chunk)))
        stats.papers_deleted = len(delete_ids)

    invalidate_library_facets_cache()
    logger.info("Deleted %s paper(s) without a local PDF on server", stats.papers_deleted)
    return stats
