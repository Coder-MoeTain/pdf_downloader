"""Orchestrate search, merge, ranking, OA detection, download, and export."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from sqlalchemy.orm import Session

from app.config import AppConfig, get_runtime_config, load_config, parse_size
from app.database.connection import init_db, session_scope
from app.database.repository import (
    attach_search_result,
    complete_search_query,
    create_search_query,
    find_existing_paper,
    paper_to_record,
    save_paper,
    upsert_download,
    upsert_provider,
)
from app.models.paper import PaperRecord, PaperStatus
from app.models.search import SearchFilters, SearchStats
from app.providers import build_providers
from app.services.dedup_service import deduplicate
from app.services.download_service import DownloadService, write_topic_metadata_csv
from app.services.export_service import ExportService
from app.services.oa_service import OpenAccessService
from app.services.progress import tracker
from app.services.query_expansion import expand_query
from app.services.ranking_service import rank_papers
from app.utils.http import AsyncHttpClient
from app.utils.logger import get_logger

logger = get_logger("app.search")
console = Console()


class SearchService:
    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or get_runtime_config()

    async def run(self, filters: SearchFilters, *, user_id: int | None = None) -> SearchStats:
        init_db()
        snap = tracker.snapshot()
        if not (snap.get("active") and snap.get("kind") == "search"):
            tracker.start_search(filters.query)
        try:
            return await self._run(filters, user_id=user_id)
        except Exception as exc:
            tracker.finish_search(error=str(exc))
            raise

    async def _run(self, filters: SearchFilters, user_id: int | None = None) -> SearchStats:
        stats = SearchStats(query=filters.query)
        expanded = expand_query(filters.query, self.config.query_expansion)
        stats.expanded_queries = expanded
        # Search APIs with the original query only; expansions improve ranking, not request volume.

        async with AsyncHttpClient(self.config) as client:
            providers = build_providers(client, self.config)
            if filters.source:
                providers = [p for p in providers if p.name == filters.source]
            stats.sources_searched = len(providers)
            if not providers:
                console.print("[yellow]No providers available. Check config.yaml and API keys.[/]")
                tracker.log("No providers available. Check Settings → Academic sources and API keys.", "warning")
                tracker.finish_search(error="No academic sources are available.")
                return stats

            tracker.set_providers_total(len(providers))
            tracker.set_phase(
                "searching",
                f"Querying {len(providers)} academic source{'s' if len(providers) != 1 else ''}…",
                current=0,
                total=len(providers),
                percent=8,
            )

            raw: list[PaperRecord] = []
            console.print()
            raw.extend(await self._search_providers(providers, filters.query, filters, stats))

            stats.raw_records = len(raw)
            console.print(f"[cyan]\\[MERGE][/] Total records............{stats.raw_records}")
            tracker.update_stats(raw_records=stats.raw_records, sources_searched=stats.sources_searched)
            tracker.set_phase("merging", f"Merging {stats.raw_records} records…", percent=48)

            unique, removed = deduplicate(raw, self.config.dedup)
            stats.duplicates_removed = removed
            unique = self._apply_filters(unique, filters)
            unique = rank_papers(unique, filters, expanded, self.config.ranking)
            unique = unique[: filters.max_results]
            stats.unique_papers = len(unique)
            stats.relevant_papers = len(unique)
            console.print(f"[cyan]\\[DEDUP][/] Unique papers............{stats.unique_papers}")
            tracker.update_stats(unique_papers=stats.unique_papers, duplicates_removed=removed)
            tracker.log(f"Deduplicated to {stats.unique_papers} unique papers ({removed} duplicates removed).")

            oa = OpenAccessService(client, self.config)
            tracker.set_phase(
                "oa",
                f"Checking open-access copies for {len(unique)} papers…",
                current=0,
                total=len(unique),
                percent=52,
            )
            with Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                BarColumn(),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task("[OA] Resolving open access", total=len(unique))
                for index, paper in enumerate(unique, start=1):
                    try:
                        await oa.resolve(paper)
                    except Exception as exc:
                        logger.warning("OA resolve failed for %s: %s", paper.title[:60], exc)
                    progress.advance(task)
                    if index == len(unique) or index % 8 == 0:
                        tracker.set_phase(
                            "oa",
                            f"Open access {index}/{len(unique)}",
                            current=index,
                            total=len(unique),
                            percent=round(52 + (index / max(len(unique), 1)) * 22, 1),
                        )

            stats.open_access_papers = sum(1 for p in unique if p.status == PaperStatus.OA_AVAILABLE or p.open_access)
            stats.paywalled = sum(1 for p in unique if p.status == PaperStatus.PAYWALLED)
            stats.no_pdf = sum(1 for p in unique if p.status == PaperStatus.NO_PDF)
            console.print(f"[green]\\[OA][/] Open access..................{stats.open_access_papers}")
            console.print(f"[yellow]\\[PAYWALL][/].........................{stats.paywalled}")
            console.print(f"[magenta]\\[NO PDF][/]..........................{stats.no_pdf}")
            tracker.update_stats(
                open_access_papers=stats.open_access_papers,
                paywalled=stats.paywalled,
                no_pdf=stats.no_pdf,
            )
            tracker.log(
                f"Open access {stats.open_access_papers} · paywalled {stats.paywalled} · no PDF {stats.no_pdf}"
            )

            tracker.set_phase("storing", f"Saving {len(unique)} papers to the library…", percent=78)
            with session_scope() as session:
                search_row = create_search_query(
                    session,
                    filters.query,
                    expanded,
                    dataclasses.asdict(filters) | {"sort": filters.sort.value},
                )
                persisted: list[tuple[int, PaperRecord]] = []
                for rank, paper in enumerate(unique, start=1):
                    db_paper = save_paper(session, paper)
                    attach_search_result(session, search_row.id, db_paper.id, rank, paper.relevance_score)
                    upsert_download(session, db_paper.id, pdf_url=paper.pdf_url, status=paper.status.value)
                    persisted.append((db_paper.id, paper))
                session.flush()

                downloader = DownloadService(client, self.config)
                download_limit = filters.download_limit or self.config.download_limit
                max_size = filters.max_file_size or self.config.max_file_size_bytes
                to_download = [
                    item
                    for item in persisted
                    if item[1].status == PaperStatus.OA_AVAILABLE and item[1].pdf_url
                ]
                if filters.open_access_only:
                    to_download = [item for item in to_download if item[1].open_access]
                to_download = to_download[:download_limit]

                if filters.download and to_download:
                    console.print()
                    tracker.start_batch(len(to_download), "Downloading open-access PDFs")
                    try:
                        for idx, (paper_id, paper) in enumerate(to_download, start=1):
                            console.print(f"[blue]\\[DOWNLOAD][/] {idx}/{len(to_download)} {paper.title[:70]}")
                            tracker.begin_item(paper_id, paper.title, idx)
                            updated = await downloader.download_paper(
                                session,
                                paper_id,
                                paper,
                                filters.topic_slug,
                                max_file_size=max_size,
                                on_progress=lambda received, total: tracker.update_bytes(received, total),
                                user_id=user_id,
                            )
                            save_paper(session, updated)
                            session.commit()
                            tracker.finish_item(updated.status.value, error=updated.extra.get("error"))
                            if updated.status == PaperStatus.DOWNLOADED:
                                stats.pdfs_downloaded += 1
                            elif updated.status == PaperStatus.FAILED:
                                stats.failed_downloads += 1
                    finally:
                        tracker.finish_batch()
                elif not filters.download:
                    console.print("[dim]Skipping downloads (--no-download).[/]")
                    tracker.log("PDF download skipped (disabled for this run).")

                complete_search_query(session, search_row.id)
                unique = [p for _, p in persisted]

            write_topic_metadata_csv(unique, filters.topic_slug, self.config)
            exporter = ExportService(self.config)
            paths = exporter.export_all(unique, stats)
            stats.library_path = str(self.config.resolve_path(self.config.library_dir) / filters.topic_slug)
            stats.report_path = str(paths["xlsx"])
            tracker.update_stats(
                pdfs_downloaded=stats.pdfs_downloaded,
                failed_downloads=stats.failed_downloads,
                unique_papers=stats.unique_papers,
            )
            tracker.finish_search(
                stats={
                    "unique_papers": stats.unique_papers,
                    "raw_records": stats.raw_records,
                    "open_access_papers": stats.open_access_papers,
                    "paywalled": stats.paywalled,
                    "pdfs_downloaded": stats.pdfs_downloaded,
                    "failed_downloads": stats.failed_downloads,
                    "duplicates_removed": stats.duplicates_removed,
                    "sources_searched": stats.sources_searched,
                }
            )
            self._print_summary(stats)
            return stats

    async def _search_providers(
        self,
        providers,
        query: str,
        filters: SearchFilters,
        stats: SearchStats,
    ) -> list[PaperRecord]:
        async def _one(provider) -> tuple[str, list[PaperRecord] | Exception]:
            try:
                results = await provider.search(query, filters)
                return provider.display_name, results
            except Exception as exc:
                logger.exception("Provider %s failed", provider.name)
                return provider.display_name, exc

        gathered_tasks = [asyncio.create_task(_one(p), name=p.display_name) for p in providers]
        raw: list[PaperRecord] = []
        with session_scope() as session:
            for fut in asyncio.as_completed(gathered_tasks):
                name, result = await fut
                if isinstance(result, Exception):
                    console.print(f"[red]\\[SEARCH][/] {name:.<28} error: {result}")
                    upsert_provider(session, name.lower().replace(" ", "_"), error=str(result))
                    stats.provider_counts[name] = 0
                    tracker.provider_finished(name, error=str(result))
                    continue
                console.print(f"[green]\\[SEARCH][/] {name:.<28} {len(result)} results")
                stats.provider_counts[name] = stats.provider_counts.get(name, 0) + len(result)
                upsert_provider(session, name.lower().replace(" ", "_"))
                tracker.provider_finished(name, count=len(result))
                raw.extend(result)
        return raw

    def _apply_filters(self, papers: list[PaperRecord], filters: SearchFilters) -> list[PaperRecord]:
        out: list[PaperRecord] = []
        for paper in papers:
            if filters.year_from and paper.publication_year and paper.publication_year < filters.year_from:
                continue
            if filters.year_to and paper.publication_year and paper.publication_year > filters.year_to:
                continue
            if filters.min_citations and (paper.citation_count or 0) < filters.min_citations:
                continue
            if filters.authors:
                names = paper.author_names.lower()
                if filters.authors.lower() not in names:
                    continue
            if filters.journal:
                hay = f"{paper.journal or ''} {paper.conference or ''}".lower()
                if filters.journal.lower() not in hay:
                    continue
            if filters.publisher and filters.publisher.lower() not in (paper.publisher or "").lower():
                continue
            out.append(paper)
        return out

    def _print_summary(self, stats: SearchStats) -> None:
        console.print()
        console.print("[bold green]Research complete.[/]")
        console.print()
        console.print(f"[bold]Search:[/]\n{stats.query}")
        console.print()
        console.print(f"{'Sources searched:':<22} {stats.sources_searched:>6}")
        console.print(f"{'Raw records:':<22} {stats.raw_records:>6}")
        console.print(f"{'Unique papers:':<22} {stats.unique_papers:>6}")
        console.print(f"{'Relevant papers:':<22} {stats.relevant_papers:>6}")
        console.print(f"{'Open-access papers:':<22} {stats.open_access_papers:>6}")
        console.print(f"{'PDFs downloaded:':<22} {stats.pdfs_downloaded:>6}")
        console.print(f"{'No PDF available:':<22} {stats.no_pdf:>6}")
        console.print(f"{'Paywalled:':<22} {stats.paywalled:>6}")
        console.print(f"{'Duplicates removed:':<22} {stats.duplicates_removed:>6}")
        console.print(f"{'Failed downloads:':<22} {stats.failed_downloads:>6}")
        console.print()
        console.print(f"[bold]Library:[/]\n{stats.library_path}")
        console.print()
        console.print(f"[bold]Report:[/]\n{stats.report_path}")
        if stats.expanded_queries[1:]:
            console.print()
            console.print("[dim]Expanded queries:[/]")
            for q in stats.expanded_queries[1:]:
                console.print(f"  - {q}")


def filters_from_cli(
    query: str,
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    authors: str | None = None,
    journal: str | None = None,
    publisher: str | None = None,
    source: str | None = None,
    open_access_only: bool = False,
    max_results: int | None = None,
    min_citations: int = 0,
    sort: str = "relevance",
    no_download: bool = False,
    download_limit: int | None = None,
    max_file_size: str | None = None,
    topic_name: str | None = None,
) -> SearchFilters:
    from app.models.search import SortMode

    cfg = get_runtime_config()
    try:
        sort_mode = SortMode(sort)
    except ValueError:
        sort_mode = SortMode.RELEVANCE
    size = parse_size(max_file_size, cfg.max_file_size_bytes) if max_file_size else cfg.max_file_size_bytes
    return SearchFilters(
        query=query,
        year_from=year_from,
        year_to=year_to,
        authors=authors,
        journal=journal,
        publisher=publisher,
        source=source,
        open_access_only=open_access_only,
        max_results=max_results or cfg.default_max_results,
        min_citations=min_citations,
        sort=sort_mode,
        download=not no_download,
        download_limit=download_limit or cfg.download_limit,
        max_file_size=size,
        topic_name=topic_name,
    )
