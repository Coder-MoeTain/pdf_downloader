"""OpenAlex Works API provider."""

from __future__ import annotations

from typing import Any

from app.models.crawl import BrowsePage, CrawlFilters
from app.models.paper import AuthorRecord, PaperRecord
from app.models.search import SearchFilters
from app.providers.base import ResearchProvider
from app.utils.doi import doi_url, normalize_doi
from app.utils.logger import get_logger
from app.utils.pdf_url import is_direct_pdf_url

logger = get_logger("app.providers.openalex")


class OpenAlexProvider(ResearchProvider):
    name = "openalex"
    display_name = "OpenAlex"
    supports_browse = True
    BASE = "https://api.openalex.org/works"

    def _search_params(self, query: str, filters: SearchFilters) -> dict[str, Any]:
        params: dict[str, Any] = {
            "search": query,
            "per_page": min(filters.max_results, 200),
            "mailto": self.config.env.polite_email,
        }
        filt: list[str] = []
        if filters.year_from and filters.year_to:
            filt.append(f"publication_year:{filters.year_from}-{filters.year_to}")
        elif filters.year_from:
            filt.append(f"from_publication_date:{filters.year_from}-01-01")
        elif filters.year_to:
            filt.append(f"to_publication_date:{filters.year_to}-12-31")
        if filters.open_access_only:
            filt.append("is_oa:true")
        if filters.min_citations:
            filt.append(f"cited_by_count:>{filters.min_citations - 1}")
        if filt:
            params["filter"] = ",".join(filt)
        return params

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params = self._search_params(query, filters)
        data = await self.request_json(self.BASE, params=params)
        results = (data or {}).get("results") or []
        papers = [self._parse(item) for item in results]
        return [p for p in papers if p and p.title]

    async def browse(self, filters: CrawlFilters, *, cursor: str | None = None) -> BrowsePage:
        params: dict[str, Any] = {
            "per_page": min(filters.page_size, 200),
            "sort": "publication_date:desc",
            "mailto": self.config.env.polite_email,
            "cursor": cursor or "*",
        }
        if filters.query.strip():
            params["search"] = filters.query.strip()
        filt: list[str] = ["type:article|preprint|posted-content"]
        if filters.year_from and filters.year_to:
            filt.append(f"publication_year:{filters.year_from}-{filters.year_to}")
        elif filters.year_from:
            filt.append(f"from_publication_date:{filters.year_from}-01-01")
        elif filters.year_to:
            filt.append(f"to_publication_date:{filters.year_to}-12-31")
        if filters.open_access_only:
            filt.append("is_oa:true")
        params["filter"] = ",".join(filt)
        data = await self.request_json(self.BASE, params=params)
        meta = (data or {}).get("meta") or {}
        results = (data or {}).get("results") or []
        records = [p for p in (self._parse(item) for item in results) if p and p.title]
        next_cursor = meta.get("next_cursor")
        return BrowsePage(
            records=records,
            next_cursor=next_cursor,
            has_more=bool(next_cursor),
            page_number=int(meta.get("page") or 1),
            total_results=meta.get("count"),
        )

    async def get_paper(self, identifier: str) -> PaperRecord | None:
        doi = normalize_doi(identifier)
        key = f"https://doi.org/{doi}" if doi else identifier
        data = await self.request_json(
            f"{self.BASE}/{key}",
            params={"mailto": self.config.env.polite_email},
        )
        return self._parse(data) if data else None

    async def find_pdf(self, paper: PaperRecord) -> str | None:
        if paper.pdf_url:
            return paper.pdf_url
        return (paper.extra or {}).get("oa_url")

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title = item.get("display_name") or item.get("title") or ""
        authors: list[AuthorRecord] = []
        for authorship in item.get("authorships") or []:
            author = authorship.get("author") or {}
            name = author.get("display_name") or ""
            if not name:
                continue
            inst = [
                (i.get("display_name") or "")
                for i in (authorship.get("institutions") or [])
                if i.get("display_name")
            ]
            authors.append(AuthorRecord(name=name, affiliations=inst, orcid=author.get("orcid")))

        primary = item.get("primary_location") or {}
        source = primary.get("source") or {}
        oa = item.get("open_access") or {}
        pdf_url = primary.get("pdf_url") or oa.get("oa_url")
        if not is_direct_pdf_url(pdf_url, prefer_https=False):
            pdf_url = None
        biblio = item.get("biblio") or {}
        ids = item.get("ids") or {}
        doi = normalize_doi(item.get("doi") or ids.get("doi"))
        keywords = [k.get("display_name") for k in (item.get("keywords") or []) if k.get("display_name")]
        concepts = [c.get("display_name") for c in (item.get("concepts") or [])[:8] if c.get("display_name")]
        openalex_id = (item.get("id") or "").rsplit("/", 1)[-1]

        journal_name = source.get("display_name")
        source_type = (source.get("type") or "").lower()
        conference = journal_name if source_type in {"conference", "proceedings"} else None

        return PaperRecord(
            title=title,
            abstract=inverted_index_to_text(item.get("abstract_inverted_index")),
            authors=authors,
            publication_year=item.get("publication_year"),
            publication_date=item.get("publication_date"),
            journal=None if conference else journal_name,
            conference=conference,
            volume=biblio.get("volume"),
            issue=biblio.get("issue"),
            pages=_pages(biblio.get("first_page"), biblio.get("last_page")),
            publisher=source.get("host_organization_name"),
            doi=doi,
            pmid=_id_tail(ids.get("pmid")),
            pmcid=_id_tail(ids.get("pmcid")),
            openalex_id=openalex_id or None,
            url=item.get("id") or doi_url(doi),
            pdf_url=pdf_url,
            citation_count=item.get("cited_by_count"),
            reference_count=item.get("referenced_works_count"),
            keywords=keywords or concepts,
            research_fields=concepts,
            open_access=oa.get("is_oa"),
            license=primary.get("license") or oa.get("oa_status"),
            source_provider=self.name,
            metadata_sources={"abstract": self.name, "open_access": self.name, "affiliations": self.name},
            extra={"oa_url": oa.get("oa_url"), "oa_status": oa.get("oa_status")},
        )


def inverted_index_to_text(inv: dict[str, list[int]] | None) -> str | None:
    if not inv:
        return None
    positions = [p for pos in inv.values() for p in pos]
    if not positions:
        return None
    words = [""] * (max(positions) + 1)
    for word, pos in inv.items():
        for p in pos:
            if 0 <= p < len(words):
                words[p] = word
    return " ".join(words).strip() or None


def _pages(first: str | None, last: str | None) -> str | None:
    if first and last:
        return f"{first}-{last}"
    return first or last


def _id_tail(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).rsplit("/", 1)[-1] or None
