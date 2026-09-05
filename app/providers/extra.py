"""Additional official API providers: DOAJ, IEEE, Springer, Elsevier, NASA ADS, NASA NTRS."""

from __future__ import annotations

from typing import Any

from app.models.crawl import BrowsePage, CrawlFilters
from app.models.paper import AuthorRecord, PaperRecord
from app.models.search import SearchFilters
from app.providers.base import ResearchProvider
from app.providers.crawl_browse import crawl_query, finish_page, page_offset
from app.utils.doi import doi_url, normalize_doi
from app.utils.http import HttpError
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
    supports_browse = True
    BASE = "https://doaj.org/api/search/articles"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        page = await self.browse(
            CrawlFilters(source=self.name, query=query, page_size=min(filters.max_results, 100)),
        )
        out = page.records
        if filters.year_from:
            out = [p for p in out if not p.publication_year or p.publication_year >= filters.year_from]
        if filters.year_to:
            out = [p for p in out if not p.publication_year or p.publication_year <= filters.year_to]
        return out

    async def browse(self, filters: CrawlFilters, *, cursor: str | None = None) -> BrowsePage:
        page_size = min(filters.page_size, 100)
        page_num, _ = page_offset(cursor, page_size)
        query = crawl_query(filters, fallback="journal")
        q = f"bibjson.title:{query} OR bibjson.abstract:{query}"
        if filters.year_from:
            q += f" AND bibjson.year:[{filters.year_from} TO {filters.year_to or 2100}]"
        data = await self.request_json(
            self.BASE,
            params={"q": q, "pageSize": page_size, "page": page_num},
        )
        items = (data or {}).get("results") or []
        total = (data or {}).get("total")
        return finish_page(
            [self._parse(item) for item in items],
            page_num=page_num,
            page_size=page_size,
            total=total,
        )

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
    supports_browse = True
    BASE = "https://ieeexploreapi.ieee.org/api/v1/search/articles"

    def has_api_key(self) -> bool:
        return bool((self.config.env.ieee_api_key or "").strip())

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        key = (self.config.env.ieee_api_key or "").strip()
        params: dict[str, Any] = {
            "querytext": query,
            "apikey": key,
            "max_records": min(filters.max_results, 25),
            "start_record": 1,
            "format": "json",
        }
        if filters.year_from:
            params["start_year"] = filters.year_from
        if filters.year_to:
            params["end_year"] = filters.year_to
        if filters.open_access_only:
            params["open_access"] = "true"
        try:
            data = await self.request_json(
                self.BASE,
                params=params,
                headers={"Accept": "application/json"},
            )
        except HttpError as exc:
            if exc.status_code == 403:
                raise HttpError(
                    "IEEE Xplore rejected the API key (Developer Inactive). "
                    "The key is stored and being sent, but IEEE has not activated Metadata Search for this app. "
                    "Check the key at https://developer.ieee.org — new keys are issued weekdays 8am–5pm ET.",
                    403,
                ) from exc
            raise
        items = (data or {}).get("articles") or []
        return [p for p in (self._parse(i) for i in items) if p and p.title]

    async def browse(self, filters: CrawlFilters, *, cursor: str | None = None) -> BrowsePage:
        page_size = min(filters.page_size, 25)
        page_num, offset = page_offset(cursor, page_size)
        key = (self.config.env.ieee_api_key or "").strip()
        params: dict[str, Any] = {
            "querytext": crawl_query(filters, fallback="*"),
            "apikey": key,
            "max_records": page_size,
            "start_record": offset + 1,
            "format": "json",
        }
        if filters.year_from:
            params["start_year"] = filters.year_from
        if filters.year_to:
            params["end_year"] = filters.year_to
        if filters.open_access_only:
            params["open_access"] = "true"
        try:
            data = await self.request_json(
                self.BASE,
                params=params,
                headers={"Accept": "application/json"},
            )
        except HttpError as exc:
            if exc.status_code == 403:
                raise HttpError(
                    "IEEE Xplore rejected the API key (Developer Inactive). "
                    "Check the key at https://developer.ieee.org.",
                    403,
                ) from exc
            raise
        items = (data or {}).get("articles") or []
        total = (data or {}).get("total_records")
        return finish_page(
            [self._parse(i) for i in items],
            page_num=page_num,
            page_size=page_size,
            total=int(total) if total is not None else None,
        )

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
    supports_browse = True
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

    async def browse(self, filters: CrawlFilters, *, cursor: str | None = None) -> BrowsePage:
        page_size = min(filters.page_size, 50)
        page_num, offset = page_offset(cursor, page_size)
        q = crawl_query(filters, fallback="research")
        if filters.year_from or filters.year_to:
            start = filters.year_from or 1800
            end = filters.year_to or 2100
            q = f"{q} year:{start}-{end}"
        if filters.open_access_only:
            q = f"openaccess:true {q}"
        params = {
            "q": q,
            "api_key": self.config.env.springer_api_key,
            "p": page_size,
            "s": offset,
        }
        data = await self.request_json(self.BASE, params=params)
        items = (data or {}).get("records") or []
        total = (data or {}).get("result") or {}
        total_count = total.get("total") if isinstance(total, dict) else None
        return finish_page(
            [self._parse(i) for i in items],
            page_num=page_num,
            page_size=page_size,
            total=int(total_count) if total_count is not None else None,
        )

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
    supports_browse = True
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

    async def browse(self, filters: CrawlFilters, *, cursor: str | None = None) -> BrowsePage:
        page_size = min(filters.page_size, 25)
        page_num, offset = page_offset(cursor, page_size)
        query = crawl_query(filters, fallback="research")
        q = f"TITLE-ABS-KEY({query})"
        if filters.year_from or filters.year_to:
            start = filters.year_from or 1800
            end = filters.year_to or 2100
            q += f" AND PUBYEAR > {start - 1} AND PUBYEAR < {end + 1}"
        params = {
            "query": q,
            "count": page_size,
            "start": offset,
            "httpAccept": "application/json",
        }
        headers = {
            "X-ELS-APIKey": self.config.env.elsevier_api_key,
            "Accept": "application/json",
        }
        data = await self.request_json(self.BASE, params=params, headers=headers)
        results = (data or {}).get("search-results") or {}
        items = results.get("entry") or []
        total = results.get("opensearch:totalResults")
        return finish_page(
            [self._parse(i) for i in items],
            page_num=page_num,
            page_size=page_size,
            total=int(total) if total is not None else None,
        )

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
        pdf_url = _ads_pdf_url(item)
        openaccess = openaccess or bool(pdf_url)
        return PaperRecord(
            title=titles[0] if isinstance(titles, list) else str(titles or ""),
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
            metadata_sources={"abstract": self.name, "pdf_url": self.name} if pdf_url else {"abstract": self.name},
        )


class NasaNtrsProvider(ResearchProvider):
    """NASA Scientific and Technical Information repository (NTRS). No API key required."""

    name = "nasa_ntrs"
    display_name = "NASA NTRS"
    supports_browse = True
    BASE = "https://ntrs.nasa.gov/api/citations/search"
    ORIGIN = "https://ntrs.nasa.gov"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "q": query,
            "page.size": min(filters.max_results, 100),
            "distribution": "PUBLIC",
        }
        if filters.year_from:
            params["published.gte"] = f"{filters.year_from}-01-01"
        if filters.year_to:
            params["published.lte"] = f"{filters.year_to}-12-31"
        if filters.open_access_only:
            params["disseminated"] = "DOCUMENT_AND_METADATA"
        data = await self.request_json(self.BASE, params=params)
        items = (data or {}).get("results") or []
        papers = [p for p in (self._parse(i) for i in items) if p and p.title]
        if filters.year_from:
            papers = [p for p in papers if not p.publication_year or p.publication_year >= filters.year_from]
        if filters.year_to:
            papers = [p for p in papers if not p.publication_year or p.publication_year <= filters.year_to]
        return papers

    async def browse(self, filters: CrawlFilters, *, cursor: str | None = None) -> BrowsePage:
        page_size = min(filters.page_size, 100)
        page_num, _ = page_offset(cursor, page_size)
        params: dict[str, Any] = {
            "q": crawl_query(filters, fallback="NASA"),
            "page.size": page_size,
            "page.number": page_num,
            "distribution": "PUBLIC",
        }
        if filters.year_from:
            params["published.gte"] = f"{filters.year_from}-01-01"
        if filters.year_to:
            params["published.lte"] = f"{filters.year_to}-12-31"
        if filters.open_access_only:
            params["disseminated"] = "DOCUMENT_AND_METADATA"
        data = await self.request_json(self.BASE, params=params)
        items = (data or {}).get("results") or []
        page_meta = (data or {}).get("page") or {}
        total = page_meta.get("totalElements")
        has_more = bool(page_meta.get("hasNext"))
        return finish_page(
            [self._parse(i) for i in items],
            page_num=page_num,
            page_size=page_size,
            total=int(total) if total is not None else None,
            has_more=has_more,
            next_cursor=str(page_num + 1) if has_more else None,
        )

    async def get_paper(self, identifier: str) -> PaperRecord | None:
        text = (identifier or "").strip()
        if not text:
            return None
        digits = text.replace("ntrs:", "").strip()
        if digits.isdigit():
            data = await self.request_json(f"{self.ORIGIN}/api/citations/{digits}")
            return self._parse(data) if data else None
        data = await self.request_json(self.BASE, params={"q": text, "page.size": 5, "distribution": "PUBLIC"})
        items = (data or {}).get("results") or []
        wanted = normalize_doi(text)
        for item in items:
            paper = self._parse(item)
            if paper and paper.title:
                if wanted and paper.doi == wanted:
                    return paper
                if not wanted:
                    return paper
        return None

    async def find_pdf(self, paper: PaperRecord) -> str | None:
        return paper.pdf_url

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        cid = item.get("id")
        authors: list[AuthorRecord] = []
        for affiliation in item.get("authorAffiliations") or []:
            meta = affiliation.get("meta") or {}
            author = meta.get("author") or {}
            name = (author.get("name") or "").strip()
            if not name:
                continue
            org = (meta.get("organization") or {}).get("name")
            authors.append(
                AuthorRecord(
                    name=name,
                    affiliations=[org] if org else [],
                    orcid=author.get("orcidId") or None,
                )
            )
        publications = item.get("publications") or []
        pub0 = publications[0] if publications else {}
        doi = normalize_doi(pub0.get("doi")) if pub0 else None
        if not doi:
            for pub in publications:
                doi = normalize_doi(pub.get("doi"))
                if doi:
                    break
        pdf_url = _ntrs_pdf_url(item, self.ORIGIN)
        has_document = str(item.get("disseminated") or "") == "DOCUMENT_AND_METADATA"
        meetings = item.get("meetings") or []
        sti = str(item.get("stiType") or "")
        conference = (meetings[0].get("name") if meetings else None) if "CONFERENCE" in sti else None
        year = _year((pub0 or {}).get("publicationDate") or item.get("distributionDate") or item.get("created"))
        keywords = [str(k) for k in (item.get("keywords") or []) if k]
        copyright_info = item.get("copyright") or {}
        license_ = copyright_info.get("licenseType") or copyright_info.get("determinationType")
        return PaperRecord(
            title=item.get("title") or "",
            abstract=item.get("abstract"),
            authors=authors,
            publication_year=year,
            publication_date=str((pub0 or {}).get("publicationDate") or item.get("distributionDate") or "")[:10] or None,
            journal=None if conference else (pub0.get("publicationName") or None),
            conference=conference,
            publisher=(item.get("center") or {}).get("name") or "NASA",
            doi=doi,
            url=f"{self.ORIGIN}/citations/{cid}" if cid else doi_url(doi),
            pdf_url=pdf_url,
            keywords=keywords,
            research_fields=list(item.get("subjectCategories") or []),
            open_access=bool(pdf_url) or has_document,
            license=license_,
            source_provider=self.name,
            metadata_sources={"abstract": self.name, "pdf_url": self.name} if pdf_url else {"abstract": self.name},
            extra={"ntrs_id": cid, "sti_type": item.get("stiType")},
        )


def _ads_pdf_url(item: dict[str, Any]) -> str | None:
    bibcode = item.get("bibcode")
    if not bibcode:
        return None
    esources = {str(e).upper() for e in (item.get("esources") or [])}
    for key in ("EPRINT_PDF", "PUB_PDF", "ADS_PDF"):
        if key in esources:
            return f"https://ui.adsabs.harvard.edu/link_gateway/{bibcode}/{key}"
    if item.get("openaccess") is True:
        return f"https://ui.adsabs.harvard.edu/link_gateway/{bibcode}/PUB_PDF"
    return None


def _ntrs_pdf_url(item: dict[str, Any], origin: str) -> str | None:
    cid = item.get("id")
    for download in item.get("downloads") or []:
        mime = str(download.get("mimetype") or "").lower()
        name = str(download.get("name") or "")
        links = download.get("links") or {}
        href = links.get("pdf") or ""
        if not href and (mime == "application/pdf" or name.lower().endswith(".pdf")):
            href = links.get("original") or ""
        if not href:
            continue
        if mime and mime != "application/pdf" and not name.lower().endswith(".pdf"):
            continue
        if href.startswith("/"):
            return f"{origin}{href}"
        if href.startswith("http"):
            return href
        if cid and name.lower().endswith(".pdf"):
            return f"{origin}/api/citations/{cid}/downloads/{name}"
    return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
