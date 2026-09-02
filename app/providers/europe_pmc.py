"""Europe PMC REST API provider."""

from __future__ import annotations

from typing import Any

from app.models.paper import AuthorRecord, PaperRecord
from app.models.search import SearchFilters
from app.providers.base import ResearchProvider
from app.utils.doi import doi_url, normalize_doi
from app.utils.logger import get_logger

logger = get_logger("app.providers.europe_pmc")


class EuropePMCProvider(ResearchProvider):
    name = "europe_pmc"
    display_name = "Europe PMC"
    BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        q = query
        if filters.year_from or filters.year_to:
            start = filters.year_from or 1800
            end = filters.year_to or 2100
            q = f"{query} AND (PUB_YEAR:[{start} TO {end}])"
        if filters.open_access_only:
            q += " AND OPEN_ACCESS:y"
        params: dict[str, Any] = {
            "query": q,
            "format": "json",
            "pageSize": min(filters.max_results, 100),
            "resultType": "core",
        }
        data = await self.request_json(self.BASE, params=params)
        items = (((data or {}).get("resultList") or {}).get("result")) or []
        papers = [self._parse(item) for item in items]
        return [p for p in papers if p and p.title]

    async def get_paper(self, identifier: str) -> PaperRecord | None:
        data = await self.request_json(
            self.BASE,
            params={"query": f"DOI:{identifier} OR EXT_ID:{identifier}", "format": "json", "resultType": "core"},
        )
        items = (((data or {}).get("resultList") or {}).get("result")) or []
        return self._parse(items[0]) if items else None

    async def find_pdf(self, paper: PaperRecord) -> str | None:
        if paper.pdf_url:
            return paper.pdf_url
        if paper.pmcid:
            pmc = paper.pmcid if str(paper.pmcid).upper().startswith("PMC") else f"PMC{paper.pmcid}"
            return f"https://europepmc.org/articles/{pmc}?pdf=render"
        return None

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        authors: list[AuthorRecord] = []
        for author in (item.get("authorList") or {}).get("author") or []:
            name = author.get("fullName") or f"{author.get('firstName', '')} {author.get('lastName', '')}".strip()
            if name:
                authors.append(AuthorRecord(name=name))
        if not authors and item.get("authorString"):
            authors = [AuthorRecord(name=part.strip()) for part in item["authorString"].split(",") if part.strip()]
        pdf_url = None
        urls = (item.get("fullTextUrlList") or {}).get("fullTextUrl") or []
        for entry in urls:
            if (entry.get("documentStyle") or "").lower() == "pdf":
                pdf_url = entry.get("url")
                break
        doi = normalize_doi(item.get("doi"))
        is_oa = str(item.get("isOpenAccess") or "").lower() in {"y", "true", "1"}
        year = item.get("pubYear")
        try:
            year_i = int(year) if year else None
        except (TypeError, ValueError):
            year_i = None
        return PaperRecord(
            title=item.get("title") or "",
            abstract=item.get("abstractText"),
            authors=authors,
            publication_year=year_i,
            journal=item.get("journalTitle"),
            volume=item.get("journalVolume"),
            issue=item.get("issue"),
            pages=item.get("pageInfo"),
            publisher=item.get("publisher"),
            doi=doi,
            pmid=str(item["pmid"]) if item.get("pmid") else None,
            pmcid=item.get("pmcid"),
            url=item.get("sourceUrl") or doi_url(doi),
            pdf_url=pdf_url,
            citation_count=item.get("citedByCount"),
            open_access=is_oa,
            source_provider=self.name,
            metadata_sources={"abstract": self.name, "pmid": self.name},
        )
