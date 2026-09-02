"""Crossref Works API provider."""

from __future__ import annotations

from typing import Any

from app.models.paper import AuthorRecord, PaperRecord
from app.models.search import SearchFilters
from app.providers.base import ResearchProvider
from app.utils.doi import doi_url, normalize_doi
from app.utils.logger import get_logger

logger = get_logger("app.providers.crossref")


class CrossrefProvider(ResearchProvider):
    name = "crossref"
    display_name = "Crossref"
    BASE = "https://api.crossref.org/works"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "query": query,
            "rows": min(filters.max_results, 100),
            "mailto": self.config.env.polite_email,
        }
        filter_parts: list[str] = []
        if filters.year_from:
            filter_parts.append(f"from-pub-date:{filters.year_from}")
        if filters.year_to:
            filter_parts.append(f"until-pub-date:{filters.year_to}")
        if filters.publisher:
            params["query.publisher-name"] = filters.publisher
        if filters.journal:
            params["query.container-title"] = filters.journal
        if filters.authors:
            params["query.author"] = filters.authors
        if filter_parts:
            params["filter"] = ",".join(filter_parts)

        data = await self.request_json(self.BASE, params=params)
        items = (((data or {}).get("message") or {}).get("items")) or []
        papers = [self._parse(item) for item in items]
        return [p for p in papers if p and p.title]

    async def get_paper(self, identifier: str) -> PaperRecord | None:
        doi = normalize_doi(identifier)
        if not doi:
            return None
        data = await self.request_json(f"{self.BASE}/{doi}", params={"mailto": self.config.env.polite_email})
        item = (data or {}).get("message")
        return self._parse(item) if item else None

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        titles = item.get("title") or []
        title = titles[0] if titles else ""
        authors: list[AuthorRecord] = []
        for author in item.get("author") or []:
            given = author.get("given") or ""
            family = author.get("family") or ""
            name = f"{given} {family}".strip() or author.get("name") or ""
            if not name:
                continue
            affiliations = [a.get("name") for a in (author.get("affiliation") or []) if a.get("name")]
            authors.append(AuthorRecord(name=name, affiliations=affiliations, orcid=author.get("ORCID")))

        date_parts = (
            ((item.get("published") or {}).get("date-parts") or [[]])[0]
            or ((item.get("published-print") or {}).get("date-parts") or [[]])[0]
            or ((item.get("published-online") or {}).get("date-parts") or [[]])[0]
        )
        year = int(date_parts[0]) if date_parts else None
        pub_date = "-".join(str(p) for p in date_parts) if date_parts else None
        container = item.get("container-title") or []
        journal = container[0] if container else None
        work_type = (item.get("type") or "").lower()
        conference = None
        if "proceedings" in work_type or "conference" in work_type:
            conference = journal
            event = item.get("event") or {}
            conference = event.get("name") or conference

        pdf_url = None
        for link in item.get("link") or []:
            content = (link.get("content-type") or "").lower()
            if "pdf" in content:
                pdf_url = link.get("URL")
                break

        licenses = item.get("license") or []
        license_url = licenses[0].get("URL") if licenses else None
        doi = normalize_doi(item.get("DOI"))
        abstract = item.get("abstract")
        if abstract:
            abstract = _strip_jats(abstract)

        return PaperRecord(
            title=title,
            abstract=abstract,
            authors=authors,
            publication_year=year,
            publication_date=pub_date,
            journal=None if conference else journal,
            conference=conference,
            volume=item.get("volume"),
            issue=item.get("issue"),
            pages=item.get("page"),
            publisher=item.get("publisher"),
            doi=doi,
            url=item.get("URL") or doi_url(doi),
            pdf_url=pdf_url,
            citation_count=item.get("is-referenced-by-count"),
            reference_count=item.get("reference-count"),
            keywords=list(item.get("subject") or []),
            license=license_url,
            source_provider=self.name,
            metadata_sources={"doi": self.name, "publisher": self.name, "journal": self.name},
        )


def _strip_jats(text: str) -> str:
    import re

    cleaned = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", cleaned).strip()
