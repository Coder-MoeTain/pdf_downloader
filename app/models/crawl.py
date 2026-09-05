"""Source crawler filters and statistics."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.paper import PaperRecord


@dataclass
class CrawlFilters:
    source: str
    query: str = ""
    year_from: int | None = None
    year_to: int | None = None
    open_access_only: bool = False
    skip_existing: bool = True
    download: bool = True
    page_size: int = 100
    max_pages: int = 0
    max_papers: int = 50000
    download_limit: int | None = None
    max_file_size: int | None = None
    topic_name: str | None = None
    pdfs_only: bool = False

    @property
    def topic_slug(self) -> str:
        from app.utils.filename import slugify

        label = self.topic_name or self.source or "crawl"
        return slugify(label, max_length=60)


@dataclass
class BrowsePage:
    records: list[PaperRecord]
    next_cursor: str | None
    has_more: bool
    page_number: int = 1
    total_results: int | None = None


@dataclass
class CrawlStats:
    source: str = ""
    pages_fetched: int = 0
    records_seen: int = 0
    skipped_existing: int = 0
    new_papers: int = 0
    open_access_papers: int = 0
    paywalled: int = 0
    no_pdf: int = 0
    pdfs_downloaded: int = 0
    failed_downloads: int = 0
