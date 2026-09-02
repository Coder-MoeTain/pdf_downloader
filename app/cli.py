"""Typer CLI and interactive menu for ResearchPaper Collector."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app import __app_name__, __version__
from app.config import load_config, parse_size
from app.database.connection import init_db, session_scope
from app.database.models import Author, Download, Paper, PaperAuthor, SearchQuery
from app.database.repository import (
    library_search,
    list_failed_downloads,
    paper_to_record,
    save_paper,
    apply_paper_filters,
)
from app.models.search import SortMode
from app.providers import provider_status
from app.services.download_service import DownloadService
from app.services.export_service import ExportService
from app.services.fulltext_service import index_pdfs, search_fulltext
from app.services.search_service import SearchService, filters_from_cli
from app.utils.http import AsyncHttpClient
from app.utils.logger import setup_logging

console = Console()
app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="Search trusted academic APIs and download legally available open-access PDFs.",
)


def _run(coro):
    return asyncio.run(coro)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    setup_logging()
    init_db()
    if ctx.invoked_subcommand is None:
        interactive_menu()


@app.command()
def search(
    query: str = typer.Argument(..., help="Research topic or search query"),
    year_from: Optional[int] = typer.Option(None, "--year-from", help="Earliest publication year"),
    year_to: Optional[int] = typer.Option(None, "--year-to", help="Latest publication year"),
    authors: Optional[str] = typer.Option(None, "--authors"),
    journal: Optional[str] = typer.Option(None, "--journal"),
    publisher: Optional[str] = typer.Option(None, "--publisher"),
    source: Optional[str] = typer.Option(None, "--source", help="Limit to one provider name"),
    open_access_only: bool = typer.Option(False, "--open-access-only"),
    max_results: Optional[int] = typer.Option(None, "--max-results"),
    min_citations: int = typer.Option(0, "--min-citations"),
    sort: str = typer.Option("relevance", "--sort", help="relevance | citations | newest"),
    no_download: bool = typer.Option(False, "--no-download"),
    download_limit: Optional[int] = typer.Option(None, "--download-limit"),
    max_file_size: Optional[str] = typer.Option(None, "--max-file-size", help="e.g. 50MB"),
) -> None:
    """Search academic sources and optionally download open-access PDFs."""
    filters = filters_from_cli(
        query,
        year_from=year_from,
        year_to=year_to,
        authors=authors,
        journal=journal,
        publisher=publisher,
        source=source,
        open_access_only=open_access_only,
        max_results=max_results,
        min_citations=min_citations,
        sort=sort,
        no_download=no_download,
        download_limit=download_limit,
        max_file_size=max_file_size,
    )
    _run(SearchService().run(filters))


@app.command()
def download(
    download_limit: Optional[int] = typer.Option(None, "--download-limit"),
    max_file_size: Optional[str] = typer.Option(None, "--max-file-size"),
) -> None:
    """Download open-access PDFs for papers that are not yet downloaded."""
    _run(_download_pending(download_limit, max_file_size))


@app.command("list")
def list_papers(
    limit: int = typer.Option(25, "--limit"),
    status: Optional[str] = typer.Option(None, "--status"),
    downloadable: bool = typer.Option(False, "--downloadable", help="Only papers with a legal PDF"),
    min_rating: int = typer.Option(0, "--min-rating", help="Minimum user rating 1–5"),
) -> None:
    """List papers stored in the local library."""
    with session_scope() as session:
        stmt = select(Paper).order_by(Paper.relevance_score.desc())
        stmt = apply_paper_filters(
            stmt, status=status or "", downloadable=downloadable, min_rating=min_rating
        )
        papers = session.scalars(stmt.limit(limit)).all()
        table = Table(title="Library")
        table.add_column("ID", justify="right")
        table.add_column("Year")
        table.add_column("Title")
        table.add_column("Status")
        table.add_column("Rating")
        table.add_column("Score")
        for paper in papers:
            table.add_row(
                str(paper.id),
                str(paper.publication_year or ""),
                (paper.title or "")[:80],
                paper.status,
                str(paper.user_rating or "—"),
                f"{paper.relevance_score:.1f}",
            )
        console.print(table)


@app.command()
def stats() -> None:
    """Show library statistics."""
    _print_stats()


@app.command()
def retry() -> None:
    """Retry failed or interrupted PDF downloads."""
    _run(_retry_failed())


@app.command()
def export(
    query: Optional[str] = typer.Option(None, "--query", help="Optional library filter"),
) -> None:
    """Export the current library to CSV/JSON/XLSX."""
    from app.models.search import SearchStats

    with session_scope() as session:
        stmt = select(Paper).options(selectinload(Paper.authors).selectinload(PaperAuthor.author)).order_by(
            Paper.relevance_score.desc()
        )
        papers = [paper_to_record(p) for p in session.scalars(stmt).unique().all()]
        if query:
            q = query.lower()
            papers = [
                p
                for p in papers
                if q in p.title.lower()
                or q in (p.abstract or "").lower()
                or q in (p.doi or "").lower()
                or q in p.author_names.lower()
            ]
        search_stats = SearchStats(
            query=query or "library",
            unique_papers=len(papers),
            raw_records=len(papers),
            open_access_papers=sum(1 for p in papers if p.open_access),
            pdfs_downloaded=sum(1 for p in papers if p.status.value == "DOWNLOADED"),
            paywalled=sum(1 for p in papers if p.status.value == "PAYWALLED"),
            failed_downloads=sum(1 for p in papers if p.status.value == "FAILED"),
        )
        paths = ExportService().export_all(papers, search_stats)
        console.print(f"Exported {len(papers)} papers to {paths['xlsx']}")


@app.command()
def providers() -> None:
    """Show configured research providers and whether they are available."""
    table = Table(title="Providers")
    table.add_column("Name")
    table.add_column("Enabled")
    table.add_column("Key required")
    table.add_column("Key present")
    table.add_column("Available")
    table.add_column("RPS")
    for row in provider_status():
        table.add_row(
            str(row["display_name"]),
            "yes" if row["enabled"] else "no",
            "yes" if row["requires_key"] else "no",
            "yes" if row["has_key"] else "no",
            "[green]yes[/]" if row["available"] else "[red]no[/]",
            str(row["requests_per_second"]),
        )
    console.print(table)


@app.command("library-search")
def library_search_cmd(query: str = typer.Argument(...), limit: int = typer.Option(25, "--limit")) -> None:
    """Search previously collected papers."""
    with session_scope() as session:
        papers = library_search(session, query, limit=limit)
        table = Table(title=f"Library search: {query}")
        table.add_column("ID", justify="right")
        table.add_column("Year")
        table.add_column("Title")
        table.add_column("DOI")
        for paper in papers:
            table.add_row(str(paper.id), str(paper.publication_year or ""), paper.title[:80], paper.doi or "")
        console.print(table)
        if not papers:
            console.print("[yellow]No matches.[/]")


@app.command("index-pdfs")
def index_pdfs_cmd() -> None:
    """Extract text from downloaded PDFs for local full-text search."""
    count = index_pdfs()
    console.print(f"Indexed {count} PDFs.")


@app.command("fulltext-search")
def fulltext_search_cmd(query: str = typer.Argument(...)) -> None:
    """Search extracted PDF text."""
    hits = search_fulltext(query)
    if not hits:
        console.print("[yellow]No full-text matches. Run `python main.py index-pdfs` first.[/]")
        return
    for hit in hits:
        console.print(f"[bold]{hit['title']}[/] ({hit['year'] or 'n/a'})")
        console.print(f"  {hit['snippet']}")
        console.print()


@app.command("update-library")
def update_library() -> None:
    """Search saved topics from config.yaml and skip papers already stored."""
    _run(_update_topics())


@app.command()
def version() -> None:
    """Print application version."""
    console.print(f"{__app_name__} {__version__}")


def interactive_menu() -> None:
    while True:
        console.print()
        console.print(
            Panel.fit(
                f"[bold]{__app_name__}[/]  [dim]v{__version__}[/]\n"
                "[dim]Official APIs · open-access PDFs · no paywall bypass[/]",
                border_style="cyan",
            )
        )
        console.print("  [bold cyan]1[/]  Search papers")
        console.print("  [bold cyan]2[/]  Search and download open-access PDFs")
        console.print("  [bold cyan]3[/]  View library")
        console.print("  [bold cyan]4[/]  Retry failed downloads")
        console.print("  [bold cyan]5[/]  Export metadata")
        console.print("  [bold cyan]6[/]  Statistics")
        console.print("  [bold cyan]7[/]  Show sources")
        console.print("  [bold cyan]8[/]  Exit")
        console.print()
        choice = Prompt.ask("Select", choices=["1", "2", "3", "4", "5", "6", "7", "8"], default="1")
        if choice == "1":
            _interactive_search(download=False)
        elif choice == "2":
            _interactive_search(download=True)
        elif choice == "3":
            list_papers(limit=50, status=None)
        elif choice == "4":
            _run(_retry_failed())
        elif choice == "5":
            export(query=None)
        elif choice == "6":
            _print_stats()
        elif choice == "7":
            providers()
            console.print("[dim]Enable/disable providers in config.yaml and supply keys in .env[/]")
        elif choice == "8":
            console.print("Goodbye.")
            raise typer.Exit()


def _interactive_search(download: bool) -> None:
    query = Prompt.ask("Research topic")
    year_from = Prompt.ask("Year from (blank to skip)", default="")
    year_to = Prompt.ask("Year to (blank to skip)", default="")
    max_results = IntPrompt.ask("Max results", default=load_config().default_max_results)
    oa_only = Confirm.ask("Open access only?", default=False)
    filters = filters_from_cli(
        query,
        year_from=int(year_from) if year_from.strip() else None,
        year_to=int(year_to) if year_to.strip() else None,
        max_results=max_results,
        open_access_only=oa_only,
        no_download=not download,
    )
    _run(SearchService().run(filters))


def _print_stats() -> None:
    with session_scope() as session:
        total = session.scalar(select(func.count(Paper.id))) or 0
        oa = session.scalar(select(func.count(Paper.id)).where(Paper.open_access.is_(True))) or 0
        downloaded = session.scalar(select(func.count(Download.id)).where(Download.status == "DOWNLOADED")) or 0
        paywalled = session.scalar(select(func.count(Paper.id)).where(Paper.status == "PAYWALLED")) or 0
        failed = session.scalar(select(func.count(Download.id)).where(Download.status == "FAILED")) or 0
        searches = session.scalar(select(func.count(SearchQuery.id))) or 0
        console.print()
        console.print("[bold]Library statistics[/]")
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_row("Papers", str(total))
        table.add_row("Open access", str(oa))
        table.add_row("PDFs downloaded", str(downloaded))
        table.add_row("Paywalled", str(paywalled))
        table.add_row("Failed downloads", str(failed))
        table.add_row("Saved searches", str(searches))
        console.print(table)
        years = session.execute(
            select(Paper.publication_year, func.count(Paper.id))
            .where(Paper.publication_year.is_not(None))
            .group_by(Paper.publication_year)
            .order_by(Paper.publication_year.desc())
            .limit(12)
        ).all()
        if years:
            console.print("  By year:           " + ", ".join(f"{y}:{c}" for y, c in years))


async def _download_pending(download_limit: int | None, max_file_size: str | None) -> None:
    cfg = load_config()
    limit = download_limit or cfg.download_limit
    max_size = parse_size(max_file_size, cfg.max_file_size_bytes) if max_file_size else cfg.max_file_size_bytes
    async with AsyncHttpClient(cfg) as client:
        downloader = DownloadService(client, cfg)
        with session_scope() as session:
            stmt = (
                select(Paper)
                .options(selectinload(Paper.authors).selectinload(PaperAuthor.author), selectinload(Paper.downloads))
                .where(Paper.status.in_(["OA_AVAILABLE", "FOUND"]))
                .limit(limit)
            )
            papers = session.scalars(stmt).unique().all()
            count = 0
            for paper in papers:
                record = paper_to_record(paper)
                if not record.pdf_url:
                    continue
                updated = await downloader.download_paper(
                    session, paper.id, record, "library", max_file_size=max_size
                )
                save_paper(session, updated)
                session.commit()
                count += 1
            console.print(f"Processed {count} pending downloads.")


async def _retry_failed() -> None:
    cfg = load_config()
    async with AsyncHttpClient(cfg) as client:
        downloader = DownloadService(client, cfg)
        with session_scope() as session:
            rows = list_failed_downloads(session)
            if not rows:
                console.print("No failed downloads to retry.")
                return
            for row in rows:
                if not row.paper:
                    continue
                record = paper_to_record(row.paper)
                record.pdf_url = row.pdf_url or record.pdf_url
                console.print(f"Retrying: {record.title[:70]}")
                await downloader.download_paper(session, row.paper_id, record, "library")
                session.commit()
            console.print(f"Retried {len(rows)} downloads.")


async def _update_topics() -> None:
    from sqlalchemy import select as sel

    cfg = load_config()
    service = SearchService(cfg)
    for topic in cfg.topics:
        console.print(f"\n[bold]Updating topic:[/] {topic.name}")
        with session_scope() as session:
            existing_dois = set(session.scalars(sel(Paper.doi).where(Paper.doi.is_not(None))).all())
        filters = filters_from_cli(
            topic.query,
            year_from=topic.year_from,
            year_to=topic.year_to,
            max_results=topic.max_results,
            open_access_only=topic.open_access_only,
            topic_name=topic.name,
        )
        stats = await service.run(filters)
        console.print(f"Topic '{topic.name}' stored {stats.unique_papers} papers (existing DOIs: {len(existing_dois)}).")
