"""Download and validate legally available PDFs."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.config import AppConfig, get_runtime_config
from app.database.models import Download, Paper
from app.database.repository import find_downloaded_by_sha256, upsert_download
from app.models.paper import PaperRecord, PaperStatus
from app.utils.filename import paper_filename, safe_join, slugify
from app.utils.http import AsyncHttpClient
from app.utils.logger import get_logger
from app.utils.pdf_url import is_direct_pdf_url
from app.utils.security import looks_like_pdf, robots_allowed
from app.services.progress import download_tracker

logger = get_logger("app.download")


def _clip_url(url: str, limit: int = 96) -> str:
    value = (url or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


class DownloadError(RuntimeError):
    pass


class DownloadService:
    def __init__(self, client: AsyncHttpClient, config: AppConfig | None = None) -> None:
        self.client = client
        self.config = config or get_runtime_config()

    def library_root(self) -> Path:
        return self.config.resolve_path(self.config.library_dir)

    def topic_dir(self, topic_slug: str, year: int | None) -> Path:
        year_part = str(year) if year else "unknown"
        path = safe_join(self.library_root(), topic_slug, year_part)
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def download_paper(
        self,
        session: Session,
        paper_id: int,
        paper: PaperRecord,
        topic_slug: str,
        *,
        max_file_size: int | None = None,
        on_progress: Callable[[int, int | None], None] | None = None,
        user_id: int | None = None,
    ) -> PaperRecord:
        max_size = max_file_size or self.config.max_file_size_bytes
        dest_dir = self.topic_dir(topic_slug, paper.publication_year)
        filename = paper_filename(
            paper.publication_year,
            paper.first_author,
            paper.title,
            paper.doi,
            max_length=self.config.max_filename_length,
        )
        dest = dest_dir / filename

        if dest.exists() and dest.stat().st_size >= self.config.min_pdf_size_bytes:
            digest = _sha256_file(dest)
            if dest.read_bytes()[:5] == b"%PDF-":
                reused = self._reuse_duplicate(
                    session, paper_id, paper, dest, digest, dest.stat().st_size, user_id=user_id
                )
                if reused:
                    return paper
                paper.status = PaperStatus.DOWNLOADED
                upsert_download(
                    session,
                    paper_id,
                    pdf_url=paper.pdf_url,
                    status=PaperStatus.DOWNLOADED.value,
                    local_path=str(dest),
                    file_size=dest.stat().st_size,
                    sha256=digest,
                    user_id=user_id,
                )
                paper.extra["local_path"] = str(dest)
                if on_progress:
                    on_progress(dest.stat().st_size, dest.stat().st_size)
                download_tracker.log(f"Already on disk: {dest.name}", "success")
                return paper

        if not paper.pdf_url or not is_direct_pdf_url(paper.pdf_url, prefer_https=self.config.prefer_https):
            paper.status = PaperStatus.NO_PDF
            upsert_download(session, paper_id, pdf_url=paper.pdf_url, status=PaperStatus.NO_PDF.value, error="No legal PDF URL")
            download_tracker.log("No legal PDF URL", "info")
            paper.extra["error"] = "No legal PDF URL"
            return paper

        if not robots_allowed(paper.pdf_url, self.config.user_agent_header()):
            paper.status = PaperStatus.SKIPPED
            upsert_download(
                session,
                paper_id,
                pdf_url=paper.pdf_url,
                status=PaperStatus.SKIPPED.value,
                error="Blocked by robots.txt",
            )
            download_tracker.log(f"Blocked by robots.txt: {_clip_url(paper.pdf_url)}", "info")
            paper.extra["error"] = "Blocked by robots.txt"
            return paper

        upsert_download(session, paper_id, pdf_url=paper.pdf_url, status=PaperStatus.DOWNLOADING.value)
        session.commit()
        download_tracker.log(f"GET {_clip_url(paper.pdf_url)}")

        try:
            size, digest = await self._stream_pdf(paper.pdf_url, dest, max_size, on_progress=on_progress)
        except DownloadError as exc:
            if dest.exists():
                dest.unlink(missing_ok=True)
            message = str(exc)
            if (
                message.startswith("Not a PDF")
                or "HTML" in message
                or "magic bytes" in message.lower()
                or "landing" in message.lower()
            ):
                status = PaperStatus.NO_PDF
            elif "HTTP 401" in message or "HTTP 403" in message:
                status = PaperStatus.PAYWALLED
            else:
                status = PaperStatus.FAILED
            paper.status = status
            upsert_download(
                session,
                paper_id,
                pdf_url=paper.pdf_url,
                status=status.value,
                error=message,
                increment_retry=True,
            )
            logger.warning("Download failed for %s: %s", paper.title[:80], exc)
            paper.extra["error"] = message
            return paper

        if self._reuse_duplicate(session, paper_id, paper, dest, digest, size, user_id=user_id):
            return paper

        paper.status = PaperStatus.DOWNLOADED
        paper.extra["local_path"] = str(dest)
        upsert_download(
            session,
            paper_id,
            pdf_url=paper.pdf_url,
            status=PaperStatus.DOWNLOADED.value,
            local_path=str(dest),
            file_size=size,
            sha256=digest,
            user_id=user_id,
        )
        logger.info("Downloaded %s (%s bytes)", dest.name, size)
        return paper

    async def _stream_pdf(
        self,
        url: str,
        dest: Path,
        max_size: int,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> tuple[int, str]:
        timeout = httpx.Timeout(
            connect=8.0,
            read=min(float(self.config.env.download_timeout_seconds), 45.0),
            write=30.0,
            pool=10.0,
        )
        hasher = hashlib.sha256()
        size = 0
        first_chunk = b""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")

        async with self.client._download_sema:
            async with self.client._client.stream(
                "GET",
                url,
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": self.config.user_agent_header()},
            ) as response:
                if response.status_code >= 400:
                    download_tracker.log(f"HTTP {response.status_code}", "danger")
                    raise DownloadError(f"HTTP {response.status_code}")
                content_type = response.headers.get("content-type", "")
                declared = response.headers.get("content-length")
                if declared and int(declared) > max_size:
                    download_tracker.log("Remote file exceeds max size", "danger")
                    raise DownloadError("Remote file exceeds max size")
                if "html" in content_type.lower() or "json" in content_type.lower():
                    download_tracker.log(f"Not a PDF (Content-Type {content_type})", "danger")
                    raise DownloadError(f"Not a PDF (Content-Type {content_type})")
                total = int(declared) if declared and declared.isdigit() else None
                ctype = content_type.split(";")[0].strip()
                header_note = f"HTTP {response.status_code}"
                if total:
                    header_note += f" · {total:,} bytes"
                if ctype:
                    header_note += f" · {ctype}"
                download_tracker.log(header_note)
                if on_progress:
                    on_progress(0, total)

                with tmp.open("wb") as handle:
                    async for chunk in response.aiter_bytes(65_536):
                        if not chunk:
                            continue
                        if not first_chunk:
                            first_chunk = chunk[:16]
                            if not looks_like_pdf(content_type, first_chunk, 1, max(len(chunk), 1)):
                                preview = chunk[:200].lower()
                                if b"<html" in preview or b"captcha" in preview or b"access denied" in preview:
                                    raise DownloadError("Received HTML/CAPTCHA/access-denied page instead of PDF")
                                if not chunk.startswith(b"%PDF-"):
                                    raise DownloadError("Missing PDF magic bytes")
                        size += len(chunk)
                        if size > max_size:
                            raise DownloadError("Download exceeded max file size")
                        hasher.update(chunk)
                        handle.write(chunk)
                        if on_progress:
                            on_progress(size, total)

        if size < self.config.min_pdf_size_bytes:
            tmp.unlink(missing_ok=True)
            raise DownloadError(f"PDF too small ({size} bytes)")
        tmp.replace(dest)
        return size, hasher.hexdigest()

    def _reuse_duplicate(
        self,
        session: Session,
        paper_id: int,
        paper: PaperRecord,
        dest: Path,
        digest: str,
        size: int,
        *,
        user_id: int | None = None,
    ) -> bool:
        """If this PDF is already stored, drop the extra copy and point at the original."""
        existing = find_downloaded_by_sha256(session, digest, exclude_paper_id=paper_id)
        if existing is None or not existing.local_path:
            return False
        other = Path(existing.local_path)
        if not other.exists() or other.read_bytes()[:5] != b"%PDF-":
            return False
        other = other.resolve()
        dest_resolved = dest.resolve() if dest.exists() else dest
        if dest.exists() and dest_resolved != other:
            dest.unlink(missing_ok=True)
        paper.status = PaperStatus.DUPLICATE
        paper.extra["local_path"] = str(other)
        paper.extra["duplicate_of"] = existing.paper_id
        upsert_download(
            session,
            paper_id,
            pdf_url=paper.pdf_url,
            status=PaperStatus.DUPLICATE.value,
            local_path=str(other),
            file_size=size,
            sha256=digest,
            user_id=user_id,
        )
        logger.info("Skipped duplicate PDF for paper %s; reused %s", paper_id, other.name)
        return True


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def existing_pdf_path(paper: Paper, library_root: Path | None = None) -> Path | None:
    """Return a stored PDF path if it exists inside the library directory."""
    root = (library_root or get_runtime_config().resolve_path(get_runtime_config().library_dir)).resolve()
    rows = sorted(paper.downloads or [], key=lambda item: item.id, reverse=True)
    for row in rows:
        if not row.local_path:
            continue
        path = Path(row.local_path)
        if not path.is_absolute():
            path = (root / path).resolve()
        else:
            path = path.resolve()
        if not str(path).startswith(str(root)):
            continue
        if path.exists() and path.stat().st_size >= get_runtime_config().min_pdf_size_bytes:
            if path.read_bytes()[:5] == b"%PDF-":
                return path
    return None


def safe_library_pdf(path_value: str | None, library_root: Path | None = None) -> Path | None:
    """Return a PDF path only if it lives inside the research library."""
    if not path_value:
        return None
    root = (library_root or get_runtime_config().resolve_path(get_runtime_config().library_dir)).resolve()
    path = Path(path_value)
    if not path.is_absolute():
        path = (root / path).resolve()
    else:
        path = path.resolve()
    if not str(path).startswith(str(root)):
        return None
    if path.exists() and path.read_bytes()[:5] == b"%PDF-":
        return path
    return None


def pdf_button_state(paper: Paper, library_root: Path | None = None) -> str:
    """UI state: download, paywalled, or unavailable."""
    if existing_pdf_path(paper, library_root):
        return "download"
    if paper.status == PaperStatus.PAYWALLED.value:
        return "paywalled"
    if paper.pdf_url and paper.status not in {PaperStatus.SKIPPED.value}:
        return "download"
    return "unavailable"


async def ensure_local_pdf(paper_id: int, topic_slug: str = "library", user_id: int | None = None) -> Path:
    """Download a legally available PDF into the library and return its path."""
    from app.database.connection import session_scope
    from app.database.models import PaperAuthor
    from app.database.repository import paper_to_record, save_paper
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    cfg = get_runtime_config()
    library_root = cfg.resolve_path(cfg.library_dir)
    download_tracker.start_batch(1, "Preparing PDF")
    with session_scope() as session:
        paper = session.scalar(
            select(Paper)
            .options(selectinload(Paper.authors).selectinload(PaperAuthor.author), selectinload(Paper.downloads))
            .where(Paper.id == paper_id)
        )
        if paper is None:
            download_tracker.finish_batch()
            raise DownloadError("Paper not found")
        existing = existing_pdf_path(paper, library_root)
        if existing:
            if user_id:
                row = session.scalar(
                    select(Download).where(Download.paper_id == paper_id).order_by(Download.id.desc())
                )
                if row is not None and row.downloaded_by_user_id is None:
                    row.downloaded_by_user_id = user_id
            download_tracker.begin_item(paper_id, paper.title, 1)
            download_tracker.log(f"Already on disk: {existing.name}", "success")
            download_tracker.update_bytes(existing.stat().st_size, existing.stat().st_size)
            download_tracker.finish_item("DOWNLOADED")
            download_tracker.finish_batch()
            from app.services.lms_watch import schedule_lms_sync

            schedule_lms_sync(paper_ids=[paper_id])
            return existing
        if paper.status == PaperStatus.PAYWALLED.value and not paper.pdf_url:
            download_tracker.finish_item("FAILED", error="Paywalled and no legal PDF URL")
            download_tracker.finish_batch()
            raise DownloadError("This paper is paywalled and has no legal PDF URL")
        record = paper_to_record(paper)
        if not record.pdf_url:
            download_tracker.finish_item("FAILED", error="No legally available PDF URL")
            download_tracker.finish_batch()
            raise DownloadError("No legally available PDF URL")

    async with AsyncHttpClient(cfg) as client:
        downloader = DownloadService(client, cfg)
        with session_scope() as session:
            paper = session.scalar(
                select(Paper)
                .options(
                    selectinload(Paper.authors).selectinload(PaperAuthor.author),
                    selectinload(Paper.downloads),
                )
                .where(Paper.id == paper_id)
            )
            if paper is None:
                raise DownloadError("Paper not found")
            record = paper_to_record(paper)
            download_tracker.begin_item(paper_id, paper.title, 1)
            updated = await downloader.download_paper(
                session,
                paper_id,
                record,
                topic_slug,
                on_progress=lambda received, total: download_tracker.update_bytes(received, total),
                user_id=user_id,
            )
            save_paper(session, updated)
            session.flush()
            paper = session.scalar(
                select(Paper).options(selectinload(Paper.downloads)).where(Paper.id == paper_id)
            )
            path = existing_pdf_path(paper, library_root) if paper else None
            download_tracker.finish_item(updated.status.value, error=updated.extra.get("error"))
            download_tracker.finish_batch()
            if path is None:
                raise DownloadError(updated.extra.get("error") or updated.status.value)
            from app.services.lms_watch import schedule_lms_sync

            schedule_lms_sync(paper_ids=[paper_id])
            return path


async def download_open_access_papers(
    *,
    search_id: int | None = None,
    limit: int | None = None,
    topic_slug: str = "library",
    user_id: int | None = None,
) -> dict[str, int]:
    """Download pending legally available PDFs, optionally limited to one search."""
    from app.database.connection import session_scope
    from app.database.models import PaperAuthor, SearchResult
    from app.database.repository import paper_to_record, save_paper
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    cfg = get_runtime_config()
    cap = limit or cfg.download_limit
    stats = {"attempted": 0, "downloaded": 0, "failed": 0, "skipped": 0}
    async with AsyncHttpClient(cfg) as client:
        downloader = DownloadService(client, cfg)
        with session_scope() as session:
            stmt = (
                select(Paper)
                .options(
                    selectinload(Paper.authors).selectinload(PaperAuthor.author),
                    selectinload(Paper.downloads),
                )
                .where(Paper.status.in_(["OA_AVAILABLE", "FOUND", "FAILED"]))
            )
            if search_id is not None:
                stmt = stmt.join(SearchResult, SearchResult.paper_id == Paper.id).where(
                    SearchResult.search_query_id == search_id
                )
            papers = session.scalars(stmt.limit(cap)).unique().all()
            jobs = [(paper.id, paper_to_record(paper)) for paper in papers]
        download_tracker.start_batch(len(jobs), "Downloading open-access PDFs")
        try:
            for index, (paper_id, record) in enumerate(jobs, start=1):
                if not record.pdf_url:
                    stats["skipped"] += 1
                    download_tracker.begin_item(paper_id, record.title, index)
                    download_tracker.finish_item("SKIPPED", error="No legal PDF URL")
                    continue
                stats["attempted"] += 1
                download_tracker.begin_item(paper_id, record.title, index)
                with session_scope() as session:
                    updated = await downloader.download_paper(
                        session,
                        paper_id,
                        record,
                        topic_slug,
                        on_progress=lambda received, total: download_tracker.update_bytes(received, total),
                        user_id=user_id,
                    )
                    save_paper(session, updated)
                download_tracker.finish_item(updated.status.value, error=updated.extra.get("error"))
                if updated.status == PaperStatus.DOWNLOADED:
                    stats["downloaded"] += 1
                elif updated.status == PaperStatus.FAILED:
                    stats["failed"] += 1
                else:
                    stats["skipped"] += 1
        finally:
            download_tracker.finish_batch()
    if stats["downloaded"] > 0:
        from app.services.lms_watch import schedule_lms_sync

        schedule_lms_sync()
    return stats


def write_topic_metadata_csv(papers: list[PaperRecord], topic_slug: str, config: AppConfig | None = None) -> Path:
    import csv

    cfg = config or get_runtime_config()
    folder = cfg.resolve_path(cfg.library_dir) / slugify(topic_slug)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "metadata.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["Title", "Authors", "Year", "Journal", "DOI", "Status", "PDF Path", "Source", "Relevance"]
        )
        for paper in papers:
            writer.writerow(
                [
                    paper.title,
                    paper.author_names,
                    paper.publication_year or "",
                    paper.journal or paper.conference or "",
                    paper.doi or "",
                    paper.status.value,
                    paper.extra.get("local_path", ""),
                    paper.source_provider,
                    paper.relevance_score,
                ]
            )
    return path
