"""Paginated source crawler that skips papers already in the local library."""

from __future__ import annotations

import asyncio

from rich.console import Console

from app.config import AppConfig, get_runtime_config
from app.database.connection import init_db, session_scope
from app.database.repository import find_existing_paper, save_paper, upsert_download
from app.models.crawl import CrawlFilters, CrawlStats
from app.models.paper import PaperRecord, PaperStatus
from app.providers import build_providers
from app.services.download_service import DownloadService, download_papers_parallel
from app.services.oa_service import OpenAccessService
from app.services.progress import ProgressTracker, tracker
from app.utils.http import AsyncHttpClient
from app.utils.logger import get_logger
from app.utils.pdf_url import is_direct_pdf_url

logger = get_logger("app.crawl")
console = Console()
# Safety cap when max_pages is 0 (meaning crawl until the API runs out).
UNLIMITED_MAX_PAGES = 10_000


def has_downloadable_pdf(paper: PaperRecord) -> bool:
    """True when OA resolution found a direct legal PDF URL."""
    return paper.status == PaperStatus.OA_AVAILABLE and bool(
        paper.pdf_url and is_direct_pdf_url(paper.pdf_url, prefer_https=False)
    )


class CrawlCancelled(Exception):
    """Raised when a source crawl is stopped by the user."""


class CrawlService:
    def __init__(self, config: AppConfig | None = None, progress: ProgressTracker | None = None) -> None:
        self.config = config or get_runtime_config()
        self._progress = progress or tracker

    async def run(
        self,
        filters: CrawlFilters,
        *,
        user_id: int | None = None,
        skip_progress_start: bool = False,
    ) -> CrawlStats:
        init_db()
        if not skip_progress_start:
            self._progress.start_crawl(filters.source)
        try:
            return await self._run(filters, user_id=user_id)
        except CrawlCancelled:
            self._progress.finish_crawl(cancelled=True)
            raise
        except asyncio.CancelledError:
            self._progress.finish_crawl(cancelled=True)
            raise
        except Exception as exc:
            self._progress.finish_crawl(error=str(exc))
            raise

    def _raise_if_cancelled(self) -> None:
        if self._progress.is_cancelled():
            raise CrawlCancelled("Source crawl stopped.")

    async def _checkpoint(self) -> None:
        self._raise_if_cancelled()
        await asyncio.sleep(0)
        self._raise_if_cancelled()

    async def _run(self, filters: CrawlFilters, *, user_id: int | None = None) -> CrawlStats:
        stats = CrawlStats(source=filters.source)
        async with AsyncHttpClient(self.config) as client:
            providers = build_providers(client, self.config)
            provider = next((p for p in providers if p.name == filters.source), None)
            if provider is None:
                raise ValueError(f"Source “{filters.source}” is not available. Check Settings → Academic sources.")
            if not getattr(provider, "supports_browse", False):
                raise ValueError(f"{provider.display_name} does not support paginated crawling yet.")

            page_limit = filters.max_pages if filters.max_pages > 0 else UNLIMITED_MAX_PAGES
            self._progress.set_phase(
                "crawling",
                f"Crawling {provider.display_name}…",
                current=0,
                total=filters.max_pages if filters.max_pages > 0 else page_limit,
                percent=5,
            )
            self._progress.log(
                f"Crawling {provider.display_name} · skip existing: {'yes' if filters.skip_existing else 'no'}"
                f" · save: {'PDFs only' if filters.pdfs_only else 'all metadata'}"
            )

            oa = OpenAccessService(client, self.config)
            downloader = DownloadService(client, self.config)
            download_limit = filters.download_limit or self.config.download_limit
            max_size = filters.max_file_size or self.config.max_file_size_bytes
            downloaded_count = 0
            cursor: str | None = None
            page_num = 0

            while page_num < page_limit:
                await self._checkpoint()
                page = await provider.browse(filters, cursor=cursor)
                page_num += 1
                stats.pages_fetched = page_num
                stats.records_seen += len(page.records)
                self._progress.set_phase(
                    "crawling",
                    f"Page {page_num} · {len(page.records)} records from {provider.display_name}",
                    current=page_num,
                    total=filters.max_pages if filters.max_pages > 0 else page_limit,
                    percent=min(75, round(5 + (page_num / max(page_limit, 1)) * 70, 1)),
                )
                self._progress.log(
                    f"Page {page_num}: {len(page.records)} records"
                    + (f" · {page.total_results:,} total" if page.total_results else "")
                )

                page_new: list[PaperRecord] = []
                for record in page.records:
                    await self._checkpoint()
                    if filters.skip_existing:
                        with session_scope() as session:
                            if find_existing_paper(session, record):
                                stats.skipped_existing += 1
                                continue
                    page_new.append(record)

                if page_new:
                    self._progress.set_phase(
                        "oa",
                        f"Checking open access for {len(page_new)} new papers…",
                        current=0,
                        total=len(page_new),
                        percent=min(78, 5 + (page_num / max(page_limit, 1)) * 73),
                    )
                    await self._resolve_open_access(oa, page_new)
                    to_save: list[PaperRecord] = []
                    for paper in page_new:
                        if has_downloadable_pdf(paper):
                            stats.open_access_papers += 1
                            to_save.append(paper)
                        elif paper.status == PaperStatus.PAYWALLED:
                            stats.paywalled += 1
                            if not filters.pdfs_only:
                                to_save.append(paper)
                        else:
                            stats.no_pdf += 1
                            if not filters.pdfs_only:
                                to_save.append(paper)

                    skipped_no_pdf = len(page_new) - len(to_save)
                    if skipped_no_pdf:
                        self._progress.log(
                            f"Skipped {skipped_no_pdf} record(s) without a downloadable PDF on page {page_num}"
                        )

                    if not to_save:
                        self._progress.update_stats(
                            pages_fetched=stats.pages_fetched,
                            records_seen=stats.records_seen,
                            skipped_existing=stats.skipped_existing,
                            new_papers=stats.new_papers,
                            open_access_papers=stats.open_access_papers,
                            paywalled=stats.paywalled,
                            no_pdf=stats.no_pdf,
                            pdfs_downloaded=stats.pdfs_downloaded,
                        )
                        if stats.records_seen >= filters.max_papers:
                            self._progress.log(f"Reached max paper limit ({filters.max_papers}).")
                            break
                        if not page.has_more or not page.next_cursor:
                            self._progress.log("No more pages from this source.")
                            break
                        cursor = page.next_cursor
                        continue

                    self._progress.set_phase("storing", f"Saving {len(to_save)} papers…", percent=80)
                    to_download: list[tuple[int, PaperRecord]] = []
                    with session_scope() as session:
                        for paper in to_save:
                            db_paper = save_paper(session, paper)
                            upsert_download(session, db_paper.id, pdf_url=paper.pdf_url, status=paper.status.value)
                            stats.new_papers += 1
                            if (
                                filters.download
                                and downloaded_count < download_limit
                                and has_downloadable_pdf(paper)
                            ):
                                to_download.append((db_paper.id, paper))

                    if to_download:
                        self._progress.start_batch(len(to_download), "Downloading open-access PDFs")
                        try:
                            results = await download_papers_parallel(
                                downloader,
                                to_download,
                                topic_slug=filters.topic_slug,
                                max_file_size=max_size,
                                user_id=user_id,
                                job_progress=self._progress,
                                use_download_tracker=False,
                                checkpoint=self._checkpoint,
                            )
                            for _paper_id, updated in results:
                                if updated.status == PaperStatus.DOWNLOADED:
                                    stats.pdfs_downloaded += 1
                                    downloaded_count += 1
                                elif updated.status == PaperStatus.FAILED:
                                    stats.failed_downloads += 1
                        finally:
                            self._progress.finish_batch()

                self._progress.update_stats(
                    pages_fetched=stats.pages_fetched,
                    records_seen=stats.records_seen,
                    skipped_existing=stats.skipped_existing,
                    new_papers=stats.new_papers,
                    open_access_papers=stats.open_access_papers,
                    paywalled=stats.paywalled,
                    no_pdf=stats.no_pdf,
                    pdfs_downloaded=stats.pdfs_downloaded,
                )

                if stats.records_seen >= filters.max_papers:
                    self._progress.log(f"Reached max paper limit ({filters.max_papers}).")
                    break
                if not page.has_more or not page.next_cursor:
                    self._progress.log("No more pages from this source.")
                    break
                cursor = page.next_cursor

            self._progress.finish_crawl(
                stats={
                    "pages_fetched": stats.pages_fetched,
                    "records_seen": stats.records_seen,
                    "skipped_existing": stats.skipped_existing,
                    "new_papers": stats.new_papers,
                    "open_access_papers": stats.open_access_papers,
                    "paywalled": stats.paywalled,
                    "no_pdf": stats.no_pdf,
                    "pdfs_downloaded": stats.pdfs_downloaded,
                    "failed_downloads": stats.failed_downloads,
                }
            )
            console.print(
                f"[green]Crawl complete[/green] · {stats.new_papers} saved · "
                f"{stats.skipped_existing} existing · {stats.no_pdf + stats.paywalled} without PDF · "
                f"{stats.pdfs_downloaded} downloaded"
            )
            return stats

    async def _resolve_open_access(self, oa: OpenAccessService, papers: list[PaperRecord]) -> None:
        if not papers:
            return
        concurrency = max(1, int(getattr(self.config, "oa_concurrency", 8) or 8))
        sem = asyncio.Semaphore(concurrency)

        async def _resolve_one(paper: PaperRecord) -> None:
            async with sem:
                await self._checkpoint()
                try:
                    await oa.resolve(paper)
                except CrawlCancelled:
                    raise
                except Exception as exc:
                    logger.warning("OA resolve failed for %s: %s", paper.title[:60], exc)

        tasks = [asyncio.create_task(_resolve_one(paper)) for paper in papers]
        for fut in asyncio.as_completed(tasks):
            await self._checkpoint()
            await fut


def filters_from_form(
    *,
    source: str,
    query: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    open_access_only: bool = False,
    skip_existing: bool = True,
    download: bool = True,
    pdfs_only: bool = False,
    page_size: int = 100,
    max_pages: int = 0,
    max_papers: int = 50000,
) -> CrawlFilters:
    cfg = get_runtime_config()
    return CrawlFilters(
        source=source.strip(),
        query=query.strip(),
        year_from=year_from,
        year_to=year_to,
        open_access_only=open_access_only,
        skip_existing=skip_existing,
        download=download,
        pdfs_only=pdfs_only,
        page_size=max(10, min(page_size, 200)),
        max_pages=max(0, min(max_pages, UNLIMITED_MAX_PAGES)) if max_pages > 0 else 0,
        max_papers=max(10, min(max_papers, 50000)),
        download_limit=cfg.download_limit,
        max_file_size=cfg.max_file_size_bytes,
        topic_name=f"crawl_{source.strip()}",
    )
