"""arXiv Atom API provider. PDFs on arXiv are legally available preprints."""

from __future__ import annotations

import re
from typing import Any

import feedparser

from app.models.crawl import BrowsePage, CrawlFilters
from app.models.paper import AuthorRecord, PaperRecord
from app.models.search import SearchFilters
from app.providers.base import ResearchProvider
from app.utils.doi import normalize_doi
from app.utils.logger import get_logger

logger = get_logger("app.providers.arxiv")

ARXIV_ID_RE = re.compile(r"arxiv\.org/abs/([0-9.]+|[a-z\-]+/\d+)", re.I)


class ArxivProvider(ResearchProvider):
    name = "arxiv"
    display_name = "arXiv"
    supports_browse = True
    BASE = "https://export.arxiv.org/api/query"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        search_query = f"all:{query}"
        if filters.authors:
            search_query += f" AND au:{filters.authors}"
        return await self._fetch(search_query, start=0, max_results=min(filters.max_results, 100), filters=filters)

    async def browse(self, filters: CrawlFilters, *, cursor: str | None = None) -> BrowsePage:
        start = int(cursor or "0")
        page_size = min(filters.page_size, 100)
        search_query = filters.query.strip() or "all:*"
        papers = await self._fetch(search_query, start=start, max_results=page_size, filters=filters)
        next_start = start + len(papers)
        has_more = len(papers) >= page_size
        return BrowsePage(
            records=papers,
            next_cursor=str(next_start) if has_more else None,
            has_more=has_more,
            page_number=(start // page_size) + 1,
            total_results=None,
        )

    async def _fetch(
        self,
        search_query: str,
        *,
        start: int,
        max_results: int,
        filters: SearchFilters | CrawlFilters,
    ) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "search_query": search_query,
            "start": start,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        xml = await self.request_text(self.BASE, params=params, allow_http=False)
        feed = feedparser.parse(xml)
        papers: list[PaperRecord] = []
        for entry in feed.entries:
            paper = self._parse(entry)
            if not paper:
                continue
            if filters.year_from and paper.publication_year and paper.publication_year < filters.year_from:
                continue
            if filters.year_to and paper.publication_year and paper.publication_year > filters.year_to:
                continue
            papers.append(paper)
        return papers

    async def get_paper(self, identifier: str) -> PaperRecord | None:
        arxiv_id = identifier.replace("arxiv:", "").strip()
        xml = await self.request_text(self.BASE, params={"id_list": arxiv_id})
        feed = feedparser.parse(xml)
        if not feed.entries:
            return None
        return self._parse(feed.entries[0])

    async def find_pdf(self, paper: PaperRecord) -> str | None:
        if paper.arxiv_id:
            return f"https://arxiv.org/pdf/{paper.arxiv_id}.pdf"
        return paper.pdf_url

    def _parse(self, entry: Any) -> PaperRecord | None:
        title = re.sub(r"\s+", " ", getattr(entry, "title", "") or "").strip()
        if not title:
            return None
        authors = [AuthorRecord(name=a.get("name")) for a in (entry.get("authors") or []) if a.get("name")]
        published = entry.get("published") or entry.get("updated") or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        arxiv_id = None
        entry_id = entry.get("id") or ""
        match = ARXIV_ID_RE.search(entry_id)
        if match:
            arxiv_id = match.group(1)
        doi = None
        pdf_url = None
        for link in entry.get("links") or []:
            href = link.get("href") or ""
            if link.get("type") == "application/pdf" or "/pdf/" in href:
                pdf_url = href.replace("http://", "https://")
            if link.get("title") == "doi":
                doi = normalize_doi(href)
        doi = doi or normalize_doi(entry.get("arxiv_doi"))
        if arxiv_id and not pdf_url:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        cats = [t.get("term") for t in (entry.get("tags") or []) if t.get("term")]
        return PaperRecord(
            title=title,
            abstract=re.sub(r"\s+", " ", entry.get("summary") or "").strip() or None,
            authors=authors,
            publication_year=year,
            publication_date=published[:10] if published else None,
            journal=(entry.get("arxiv_journal_ref") if hasattr(entry, "arxiv_journal_ref") else None),
            publisher="arXiv",
            doi=doi,
            arxiv_id=arxiv_id,
            url=entry_id.replace("http://", "https://"),
            pdf_url=pdf_url,
            keywords=cats,
            open_access=True,
            license="arXiv.org perpetual, non-exclusive license",
            source_provider=self.name,
            metadata_sources={"arxiv_id": self.name, "pdf_url": self.name},
        )
