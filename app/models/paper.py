"""Paper and author records shared by providers and services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.utils.time import utc_now


class PaperStatus(str, Enum):
    FOUND = "FOUND"
    OA_AVAILABLE = "OA_AVAILABLE"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    PAYWALLED = "PAYWALLED"
    NO_PDF = "NO_PDF"
    FAILED = "FAILED"
    DUPLICATE = "DUPLICATE"
    SKIPPED = "SKIPPED"


@dataclass
class AuthorRecord:
    name: str
    affiliations: list[str] = field(default_factory=list)
    orcid: str | None = None


@dataclass
class PaperRecord:
    title: str
    abstract: str | None = None
    authors: list[AuthorRecord] = field(default_factory=list)
    publication_year: int | None = None
    publication_date: str | None = None
    journal: str | None = None
    conference: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publisher: str | None = None
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    semantic_scholar_id: str | None = None
    url: str | None = None
    pdf_url: str | None = None
    citation_count: int | None = None
    reference_count: int | None = None
    keywords: list[str] = field(default_factory=list)
    research_fields: list[str] = field(default_factory=list)
    open_access: bool | None = None
    license: str | None = None
    source_provider: str = ""
    metadata_sources: dict[str, str] = field(default_factory=dict)
    relevance_score: float = 0.0
    status: PaperStatus = PaperStatus.FOUND
    retrieved_at: datetime = field(default_factory=utc_now)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def first_author(self) -> str:
        return self.authors[0].name if self.authors else "Unknown"

    @property
    def author_names(self) -> str:
        return "; ".join(a.name for a in self.authors)

    def identifiers(self) -> dict[str, str]:
        values: dict[str, str] = {}
        if self.doi:
            values["doi"] = self.doi
        if self.pmid:
            values["pmid"] = self.pmid
        if self.arxiv_id:
            values["arxiv_id"] = self.arxiv_id
        if self.openalex_id:
            values["openalex_id"] = self.openalex_id
        if self.semantic_scholar_id:
            values["semantic_scholar_id"] = self.semantic_scholar_id
        if self.pmcid:
            values["pmcid"] = self.pmcid
        return values
