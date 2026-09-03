"""Semantic Scholar Graph API provider."""

from __future__ import annotations

from typing import Any

from app.models.paper import AuthorRecord, PaperRecord
from app.models.search import SearchFilters
from app.providers.base import ResearchProvider
from app.utils.doi import doi_url, normalize_doi
from app.utils.http import HttpError
from app.utils.logger import get_logger

logger = get_logger("app.providers.semantic_scholar")

FIELDS = ",".join(
    [
        "paperId",
        "title",
        "abstract",
        "authors",
        "year",
        "venue",
        "publicationVenue",
        "publicationDate",
        "externalIds",
        "citationCount",
        "referenceCount",
        "openAccessPdf",
        "fieldsOfStudy",
        "publicationTypes",
        "journal",
        "url",
        "s2FieldsOfStudy",
    ]
)


class SemanticScholarProvider(ResearchProvider):
    name = "semantic_scholar"
    display_name = "Semantic Scholar"
    BASE = "https://api.semanticscholar.org/graph/v1/paper/search"

    def has_api_key(self) -> bool:
        return bool(self.config.env.semantic_scholar_api_key)

    def _headers(self) -> dict[str, str]:
        key = self.config.env.semantic_scholar_api_key
        return {"x-api-key": key} if key else {}

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "query": query,
            "limit": min(filters.max_results, 100),
            "fields": FIELDS,
        }
        if filters.year_from or filters.year_to:
            start = filters.year_from or 1900
            end = filters.year_to or 2100
            params["year"] = f"{start}-{end}"

        try:
            data = await self.request_json(self.BASE, params=params, headers=self._headers())
        except HttpError as exc:
            if exc.status_code == 429:
                hint = (
                    "Semantic Scholar rate-limited this IP (HTTP 429). "
                    "Add SEMANTIC_SCHOLAR_API_KEY in Settings for a higher quota: "
                    "https://www.semanticscholar.org/product/api#api-key-form"
                )
                raise HttpError(hint, 429) from exc
            raise
        items = (data or {}).get("data") or []
        papers = [self._parse(item) for item in items]
        return [p for p in papers if p and p.title]

    async def get_paper(self, identifier: str) -> PaperRecord | None:
        doi = normalize_doi(identifier)
        key = f"DOI:{doi}" if doi else identifier
        data = await self.request_json(
            f"https://api.semanticscholar.org/graph/v1/paper/{key}",
            params={"fields": FIELDS},
            headers=self._headers(),
        )
        return self._parse(data) if data else None

    async def find_pdf(self, paper: PaperRecord) -> str | None:
        return paper.pdf_url

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item or item.get("error"):
            return None
        authors = [
            AuthorRecord(name=a.get("name"), affiliations=[])
            for a in (item.get("authors") or [])
            if a.get("name")
        ]
        ext = item.get("externalIds") or {}
        journal_info = item.get("journal") or {}
        venue_info = item.get("publicationVenue") or {}
        oa = item.get("openAccessPdf") or {}
        pub_types = [t.lower() for t in (item.get("publicationTypes") or [])]
        is_conf = any("conference" in t for t in pub_types)
        conference = venue_info.get("name") if is_conf else None
        journal = journal_info.get("name") or item.get("venue")
        fields = item.get("fieldsOfStudy") or [
            f.get("category") for f in (item.get("s2FieldsOfStudy") or []) if f.get("category")
        ]
        doi = normalize_doi(ext.get("DOI"))
        return PaperRecord(
            title=item.get("title") or "",
            abstract=item.get("abstract"),
            authors=authors,
            publication_year=item.get("year"),
            publication_date=item.get("publicationDate"),
            journal=journal if not conference else None,
            conference=conference,
            volume=journal_info.get("volume"),
            pages=journal_info.get("pages"),
            doi=doi,
            pmid=str(ext["PubMed"]) if ext.get("PubMed") else None,
            arxiv_id=ext.get("ArXiv"),
            semantic_scholar_id=item.get("paperId"),
            url=item.get("url") or doi_url(doi),
            pdf_url=(oa or {}).get("url"),
            citation_count=item.get("citationCount"),
            reference_count=item.get("referenceCount"),
            research_fields=[f for f in fields if f],
            open_access=bool(oa.get("url")) if oa else None,
            license=oa.get("status") if oa else None,
            source_provider=self.name,
            metadata_sources={"abstract": self.name, "citation_count": self.name},
        )
