"""CORE v3 API provider (requires CORE_API_KEY)."""

from __future__ import annotations

from typing import Any

from app.models.crawl import BrowsePage, CrawlFilters
from app.models.paper import AuthorRecord, PaperRecord
from app.models.search import SearchFilters
from app.providers.base import ResearchProvider
from app.providers.crawl_browse import crawl_query, finish_page, page_offset
from app.utils.doi import doi_url, normalize_doi
from app.utils.logger import get_logger

logger = get_logger("app.providers.core")


class CoreProvider(ResearchProvider):
    name = "core"
    display_name = "CORE"
    supports_browse = True
    BASE = "https://api.core.ac.uk/v3/search/works"

    def has_api_key(self) -> bool:
        return bool(self.config.env.core_api_key)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.env.core_api_key}"}

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        q = query
        if filters.year_from or filters.year_to:
            start = filters.year_from or 1800
            end = filters.year_to or 2100
            q = f"{query} AND yearPublished>={start} AND yearPublished<={end}"
        payload = {
            "q": q,
            "limit": min(filters.max_results, 100),
            "offset": 0,
        }
        data = await self.client.request(
            "POST",
            self.BASE,
            provider=self.name,
            requests_per_second=self.requests_per_second,
            headers=self._headers(),
            json=payload,
        )
        if data.status_code >= 400:
            logger.warning("CORE search failed: HTTP %s", data.status_code)
            return []
        body = data.json()
        items = body.get("results") or []
        papers = [self._parse(item) for item in items]
        return [p for p in papers if p and p.title]

    async def browse(self, filters: CrawlFilters, *, cursor: str | None = None) -> BrowsePage:
        page_size = min(filters.page_size, 100)
        page_num, offset = page_offset(cursor, page_size)
        q = crawl_query(filters, fallback="research")
        if filters.year_from or filters.year_to:
            start = filters.year_from or 1800
            end = filters.year_to or 2100
            q = f"{q} AND yearPublished>={start} AND yearPublished<={end}"
        payload = {"q": q, "limit": page_size, "offset": offset}
        data = await self.client.request(
            "POST",
            self.BASE,
            provider=self.name,
            requests_per_second=self.requests_per_second,
            headers=self._headers(),
            json=payload,
        )
        if data.status_code >= 400:
            logger.warning("CORE browse failed: HTTP %s", data.status_code)
            return finish_page([], page_num=page_num, page_size=page_size, has_more=False)
        body = data.json()
        items = body.get("results") or []
        total = body.get("totalHits")
        return finish_page(
            [self._parse(item) for item in items],
            page_num=page_num,
            page_size=page_size,
            total=total,
        )

    async def get_paper(self, identifier: str) -> PaperRecord | None:
        doi = normalize_doi(identifier) or identifier
        data = await self.request_json(
            f"https://api.core.ac.uk/v3/works/{doi}",
            headers=self._headers(),
        )
        return self._parse(data)

    async def find_pdf(self, paper: PaperRecord) -> str | None:
        return paper.pdf_url

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        authors: list[AuthorRecord] = []
        for author in item.get("authors") or []:
            name = author.get("name") if isinstance(author, dict) else str(author)
            if name:
                authors.append(AuthorRecord(name=name))
        year = item.get("yearPublished")
        try:
            year_i = int(year) if year else None
        except (TypeError, ValueError):
            year_i = None
        doi = normalize_doi(item.get("doi"))
        download = item.get("downloadUrl") or item.get("sourceFulltextUrls")
        pdf_url = None
        if isinstance(download, str):
            pdf_url = download
        elif isinstance(download, list) and download:
            pdf_url = download[0]
        return PaperRecord(
            title=item.get("title") or "",
            abstract=item.get("abstract"),
            authors=authors,
            publication_year=year_i,
            journal=(item.get("journals") or [{}])[0].get("title") if item.get("journals") else None,
            publisher=item.get("publisher"),
            doi=doi,
            url=item.get("sourceFulltextUrls")[0] if item.get("sourceFulltextUrls") else doi_url(doi),
            pdf_url=pdf_url,
            citation_count=item.get("citationCount"),
            open_access=True,
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name},
        )
