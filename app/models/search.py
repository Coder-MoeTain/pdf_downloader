"""Search filters and result statistics."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SortMode(str, Enum):
    RELEVANCE = "relevance"
    CITATIONS = "citations"
    NEWEST = "newest"


@dataclass
class SearchFilters:
    query: str
    year_from: int | None = None
    year_to: int | None = None
    authors: str | None = None
    journal: str | None = None
    publisher: str | None = None
    source: str | None = None
    open_access_only: bool = False
    max_results: int = 50
    min_citations: int = 0
    sort: SortMode = SortMode.RELEVANCE
    download: bool = True
    download_limit: int | None = None
    max_file_size: int | None = None
    topic_name: str | None = None

    @property
    def topic_slug(self) -> str:
        from app.utils.filename import slugify

        return slugify(self.topic_name or self.query, max_length=60)


@dataclass
class SearchStats:
    query: str = ""
    expanded_queries: list[str] = field(default_factory=list)
    sources_searched: int = 0
    raw_records: int = 0
    unique_papers: int = 0
    relevant_papers: int = 0
    open_access_papers: int = 0
    pdfs_downloaded: int = 0
    no_pdf: int = 0
    paywalled: int = 0
    duplicates_removed: int = 0
    failed_downloads: int = 0
    library_path: str = ""
    report_path: str = ""
    provider_counts: dict[str, int] = field(default_factory=dict)
