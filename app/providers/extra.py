"""Additional official API providers: DOAJ, IEEE, Springer, Elsevier, NASA ADS."""

from __future__ import annotations

from typing import Any

from app.models.paper import AuthorRecord, PaperRecord
from app.models.search import SearchFilters
from app.providers.base import ResearchProvider
from app.utils.doi import doi_url, normalize_doi
from app.utils.logger import get_logger

logger = get_logger("app.providers.extra")


def _year(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value)
    digits = "".join(ch for ch in text[:4] if ch.isdigit())
    return int(digits) if len(digits) == 4 else None


class DoajProvider(ResearchProvider):
    name = "doaj"
    display_name = "DOAJ"
    BASE = "https://doaj.org/api/search/articles"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        q = f"bibjson.title:{query} OR bibjson.abstract:{query}"
        if filters.year_from:
            q += f" AND bibjson.year:[{filters.year_from} TO {filters.year_to or 2100}]"
        url = f"{self.BASE}/{query}"
        data = await self.request_json(
            url,
            params={"pageSize": min(filters.max_results, 100), "page": 1},
        )
        items = (data or {}).get("results") or []
        papers = [self._parse(item) for item in items]
        out = [p for p in papers if p and p.title]
        if filters.year_from:
            out = [p for p in out if not p.publication_year or p.publication_year >= filters.year_from]
        if filters.year_to:
            out = [p for p in out if not p.publication_year or p.publication_year <= filters.year_to]
        return out

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        bib = item.get("bibjson") or {}
        authors = [AuthorRecord(name=a.get("name")) for a in (bib.get("author") or []) if a.get("name")]
        ident = {i.get("type"): i.get("id") for i in (bib.get("identifier") or [])}
        doi = normalize_doi(ident.get("doi"))
        links = bib.get("link") or []
        pdf_url = None
        html_url = None
        for link in links:
            href = link.get("url")
            if (link.get("type") or "").lower() == "pdf" or (href or "").lower().endswith(".pdf"):
                pdf_url = href
            else:
                html_url = html_url or href
        journal = (bib.get("journal") or {}).get("title")
        return PaperRecord(
            title=bib.get("title") or "",
            abstract=bib.get("abstract"),
            authors=authors,
            publication_year=_year(bib.get("year")),
            journal=journal,
            publisher=(bib.get("journal") or {}).get("publisher"),
            doi=doi,
            url=html_url or doi_url(doi),
            pdf_url=pdf_url,
            keywords=[k.get("term") or k if isinstance(k, str) else k.get("term") for k in (bib.get("keywords") or [])],
            open_access=True,
            license="; ".join(
                (lic.get("title") or lic.get("type") or "") for lic in (bib.get("license") or []) if isinstance(lic, dict)
            )
            or None,
            source_provider=self.name,
            metadata_sources={"open_access": self.name},
        )


class IeeeProvider(ResearchProvider):
    name = "ieee"
    display_name = "IEEE Xplore"
    BASE = "https://ieeexploreapi.ieee.org/api/v1/search/articles"

    def has_api_key(self) -> bool:
        return bool(self.config.env.ieee_api_key)

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "querytext": query,
            "apikey": self.config.env.ieee_api_key,
            "max_records": min(filters.max_results, 25),
            "format": "json",
        }
        if filters.year_from:
            params["start_year"] = filters.year_from
        if filters.year_to:
            params["end_year"] = filters.year_to
        if filters.open_access_only:
            params["open_access"] = "true"
        data = await self.request_json(self.BASE, params=params)
        items = (data or {}).get("articles") or []
        return [p for p in (self._parse(i) for i in items) if p and p.title]

    def _parse(self, item: dict[str, Any]) -> PaperRecord | None:
        authors = [
            AuthorRecord(name=a.get("full_name") or a.get("author"))
            for a in ((item.get("authors") or {}).get("authors") or [])
            if a.get("full_name") or a.get("author")
        ]
        doi = normalize_doi(item.get("doi"))
        pdf_url = item.get("pdf_url") if str(item.get("access_type") or "").lower() in {"open", "oa"} else None
        return PaperRecord(
            title=item.get("title") or "",
            abstract=item.get("abstract"),
            authors=authors,
            publication_year=_year(item.get("publication_year")),
            journal=item.get("publication_title"),
            publisher="IEEE",
            doi=doi,
            url=item.get("html_url") or doi_url(doi),
            pdf_url=pdf_url,
            citation_count=item.get("citing_paper_count"),
            open_access=str(item.get("access_type") or "").lower() in {"open", "oa"},
            source_provider=self.name,
            metadata_sources={"doi": self.name},
        )


class SpringerProvider(ResearchProvider):
    name = "springer"
    display_name = "Springer Nature"
    BASE = "https://api.springernature.com/meta/v2/json"

    def has_api_key(self) -> bool:
        return bool(self.config.env.springer_api_key)

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        q = query
        if filters.year_from or filters.year_to:
            start = filters.year_from or 1800
            end = filters.year_to or 2100
            q = f"{query} year:{start}-{end}"
        if filters.open_access_only:
            q = f"openaccess:true {q}"
        params = {
            "q": q,
            "api_key": self.config.env.springer_api_key,
            "p": min(filters.max_results, 50),
        }
        data = await self.request_json(self.BASE, params=params)
        items = (data or {}).get("records") or []
        return [p for p in (self._parse(i) for i in items) if p and p.title]

    def _parse(self, item: dict[str, Any]) -> PaperRecord | None:
        authors = [AuthorRecord(name=a.get("creator")) for a in (item.get("creators") or []) if a.get("creator")]
        doi = normalize_doi(item.get("doi"))
        pdf_url = None
        url = None
        for link in item.get("url") or []:
            if (link.get("format") or "").lower() == "pdf":
                pdf_url = link.get("value")
            else:
                url = url or link.get("value")
        oa = str(item.get("openaccess") or "").lower() in {"true", "yes"}
        return PaperRecord(
            title=item.get("title") or "",
            abstract=item.get("abstract"),
            authors=authors,
            publication_year=_year(item.get("publicationDate")),
            publication_date=item.get("publicationDate"),
            journal=item.get("publicationName"),
            publisher=item.get("publisher") or "Springer Nature",
            volume=item.get("volume"),
            issue=item.get("number"),
            doi=doi,
            url=url or doi_url(doi),
            pdf_url=pdf_url if oa else None,
            open_access=oa,
            license=item.get("license"),
            source_provider=self.name,
            metadata_sources={"publisher": self.name},
        )


class ElsevierProvider(ResearchProvider):
    name = "elsevier"
    display_name = "Elsevier"
    BASE = "https://api.elsevier.com/content/search/scopus"

    def has_api_key(self) -> bool:
        return bool(self.config.env.elsevier_api_key)

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        q = f"TITLE-ABS-KEY({query})"
        if filters.year_from or filters.year_to:
            start = filters.year_from or 1800
            end = filters.year_to or 2100
            q += f" AND PUBYEAR > {start - 1} AND PUBYEAR < {end + 1}"
        params = {
            "query": q,
            "count": min(filters.max_results, 25),
            "httpAccept": "application/json",
        }
        headers = {
            "X-ELS-APIKey": self.config.env.elsevier_api_key,
            "Accept": "application/json",
        }
        data = await self.request_json(self.BASE, params=params, headers=headers)
        items = (((data or {}).get("search-results") or {}).get("entry")) or []
        return [p for p in (self._parse(i) for i in items) if p and p.title]

    def _parse(self, item: dict[str, Any]) -> PaperRecord | None:
        if item.get("error"):
            return None
        authors = []
        for author in item.get("author") or []:
            name = author.get("authname") or f"{author.get('given-name', '')} {author.get('surname', '')}".strip()
            if name:
                authors.append(AuthorRecord(name=name))
        doi = normalize_doi(item.get("prism:doi"))
        oa = (item.get("openaccessFlag") is True) or str(item.get("openaccess") or "") in {"1", "true"}
        return PaperRecord(
            title=item.get("dc:title") or "",
            abstract=item.get("dc:description"),
            authors=authors,
            publication_year=_year(item.get("prism:coverDate")),
            publication_date=item.get("prism:coverDate"),
            journal=item.get("prism:publicationName"),
            publisher="Elsevier",
            volume=item.get("prism:volume"),
            issue=item.get("prism:issueIdentifier"),
            pages=item.get("prism:pageRange"),
            doi=doi,
            url=item.get("prism:url") or doi_url(doi),
            citation_count=_safe_int(item.get("citedby-count")),
            open_access=oa,
            source_provider=self.name,
            metadata_sources={"doi": self.name},
        )


class NasaAdsProvider(ResearchProvider):
    name = "nasa_ads"
    display_name = "NASA ADS"
    BASE = "https://api.adsabs.harvard.edu/v1/search/query"

    def has_api_key(self) -> bool:
        return bool(self.config.env.nasa_ads_token)

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        q = query
        if filters.year_from or filters.year_to:
            start = filters.year_from or 1800
            end = filters.year_to or 2100
            q = f"{query} year:{start}-{end}"
        params = {
            "q": q,
            "fl": "title,author,year,doi,bibcode,abstract,pub,citation_count,identifier,openaccess,esources",
            "rows": min(filters.max_results, 50),
        }
        headers = {"Authorization": f"Bearer {self.config.env.nasa_ads_token}"}
        data = await self.request_json(self.BASE, params=params, headers=headers)
        items = (((data or {}).get("response") or {}).get("docs")) or []
        return [p for p in (self._parse(i) for i in items) if p and p.title]

    def _parse(self, item: dict[str, Any]) -> PaperRecord | None:
        titles = item.get("title") or [""]
        authors = [AuthorRecord(name=n) for n in (item.get("author") or [])]
        dois = item.get("doi") or []
        doi = normalize_doi(dois[0] if dois else None)
        openaccess = item.get("openaccess") is True
        pdf_url = None
        if openaccess and item.get("bibcode"):
            pdf_url = f"https://ui.adsabs.harvard.edu/link_gateway/{item['bibcode']}/PUB_PDF"
        return PaperRecord(
            title=titles[0],
            abstract=item.get("abstract"),
            authors=authors,
            publication_year=_year(item.get("year")),
            journal=item.get("pub"),
            publisher="NASA ADS",
            doi=doi,
            url=f"https://ui.adsabs.harvard.edu/abs/{item.get('bibcode')}" if item.get("bibcode") else doi_url(doi),
            pdf_url=pdf_url,
            citation_count=item.get("citation_count"),
            open_access=openaccess,
            source_provider=self.name,
            metadata_sources={"abstract": self.name},
        )


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
