"""Additional no-key academic APIs. Official HTTP endpoints only; PDFs when marked OA."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

from app.models.paper import AuthorRecord, PaperRecord
from app.models.search import SearchFilters
from app.providers.base import ResearchProvider
from app.providers.crossref import CrossrefProvider
from app.providers.free import (
    _as_list,
    _authors_from,
    _dspace_first,
    _dspace_meta,
    _finished,
    _https,
    _pick_pdf,
    _text,
    _year,
)
from app.utils.doi import doi_url, normalize_doi

_HTML_TAG = re.compile(r"<[^>]+>")
_PEERJ_DOI = re.compile(r"peerj(?:-([a-z]+))?\.(\d+)$", re.IGNORECASE)
_DC_NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcndl": "http://ndl.go.jp/dcndl/terms/",
}


def _plain(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    return " ".join(_HTML_TAG.sub(" ", text).split()) or None


def _content_value(content: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        val = content.get(key)
        if isinstance(val, dict):
            text = _text(val.get("value") or val.get("html") or val.get("title"))
        else:
            text = _text(val)
        if text:
            return text
    return None


def _epoch_year(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = int(value)
        if ts > 10_000_000_000:
            ts //= 1000
        if ts > 10_000_000:
            return datetime.fromtimestamp(ts, tz=timezone.utc).year
        return ts if 1000 <= ts <= 2100 else None
    return _year(value)


def _content_list(content: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        val = content.get(key)
        if isinstance(val, dict):
            val = val.get("value")
        out = [t for t in (_text(v) for v in _as_list(val)) if t]
        if out:
            return out
    return []


class _CrossrefFilterProvider(ResearchProvider):
    """Crossref Works search narrowed by container title and/or DOI prefix."""

    container: str = ""
    prefix: str = ""
    work_type: str = ""
    assume_oa: bool = False
    publisher_name: str = ""

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        filter_parts: list[str] = []
        if self.container:
            filter_parts.append(f"container-title:{self.container}")
        if self.prefix:
            filter_parts.append(f"prefix:{self.prefix}")
        if self.work_type:
            filter_parts.append(f"type:{self.work_type}")
        if filters.year_from:
            filter_parts.append(f"from-pub-date:{filters.year_from}")
        if filters.year_to:
            filter_parts.append(f"until-pub-date:{filters.year_to}")
        params: dict[str, Any] = {
            "query": query,
            "rows": min(filters.max_results, 50),
            "mailto": self.config.env.polite_email,
        }
        if filter_parts:
            params["filter"] = ",".join(filter_parts)
        data = await self.request_json("https://api.crossref.org/works", params=params)
        items = (((data or {}).get("message") or {}).get("items")) or []
        parser = CrossrefProvider(self.client, self.config)
        papers: list[PaperRecord | None] = []
        for item in items:
            paper = parser._parse(item)
            if not paper:
                continue
            paper.source_provider = self.name
            if self.assume_oa:
                paper.open_access = True
            if self.publisher_name:
                paper.publisher = paper.publisher or self.publisher_name
            if self.container:
                paper.journal = paper.journal or self.container
            paper = self._after_parse(paper)
            papers.append(paper)
        return _finished(papers, filters)

    def _after_parse(self, paper: PaperRecord) -> PaperRecord:
        return paper


class ChemrxivProvider(_CrossrefFilterProvider):
    name = "chemrxiv"
    display_name = "ChemRxiv"
    container = "ChemRxiv"
    assume_oa = True
    publisher_name = "ChemRxiv"


class SsrnProvider(_CrossrefFilterProvider):
    name = "ssrn"
    display_name = "SSRN"
    prefix = "10.2139"
    publisher_name = "SSRN"


class ResearchSquareProvider(_CrossrefFilterProvider):
    name = "research_square"
    display_name = "Research Square"
    prefix = "10.21203"
    assume_oa = True
    publisher_name = "Research Square"


class TechrxivProvider(_CrossrefFilterProvider):
    name = "techrxiv"
    display_name = "TechRxiv"
    prefix = "10.36227"
    assume_oa = True
    publisher_name = "TechRxiv"


class F1000ResearchProvider(_CrossrefFilterProvider):
    name = "f1000research"
    display_name = "F1000Research"
    container = "F1000Research"
    assume_oa = True
    publisher_name = "F1000Research"


class NberProvider(_CrossrefFilterProvider):
    name = "nber"
    display_name = "NBER"
    prefix = "10.3386"
    publisher_name = "National Bureau of Economic Research"


class EartharxivProvider(_CrossrefFilterProvider):
    name = "eartharxiv"
    display_name = "EarthArXiv"
    container = "EarthArXiv"
    assume_oa = True
    publisher_name = "EarthArXiv"


class PeerjProvider(_CrossrefFilterProvider):
    name = "peerj"
    display_name = "PeerJ"
    prefix = "10.7717"
    assume_oa = True
    publisher_name = "PeerJ"

    def _after_parse(self, paper: PaperRecord) -> PaperRecord:
        if paper.pdf_url or not paper.doi:
            return paper
        match = _PEERJ_DOI.search(paper.doi)
        if not match:
            return paper
        series, number = match.group(1), match.group(2)
        slug = f"{series}-{number}" if series else number
        paper.pdf_url = f"https://peerj.com/articles/{slug}.pdf"
        paper.metadata_sources = {**paper.metadata_sources, "pdf_url": self.name}
        return paper


class OpenreviewProvider(ResearchProvider):
    name = "openreview"
    display_name = "OpenReview"
    BASE = "https://api2.openreview.net/notes/search"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params = {"term": query, "limit": min(filters.max_results, 50), "offset": 0}
        data = await self.request_json(self.BASE, params=params)
        notes = (data or {}).get("notes") or []
        return _finished([self._parse(item) for item in notes], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        content = item.get("content") or {}
        title = _content_value(content, "title") or _text(item.get("title"))
        if not title:
            return None
        note_id = _text(item.get("id") or item.get("forum"))
        authors = [AuthorRecord(name=n) for n in _content_list(content, "authors") if n]
        venue = _content_value(content, "venue", "venueid")
        pdf_path = _content_value(content, "pdf")
        pdf_url = None
        if pdf_path:
            pdf_url = pdf_path if pdf_path.startswith("http") else f"https://openreview.net{pdf_path}"
        elif note_id:
            pdf_url = f"https://openreview.net/pdf?id={note_id}"
        year = _epoch_year(item.get("pdate") or item.get("cdate") or item.get("tcdate"))
        return PaperRecord(
            title=title,
            abstract=_content_value(content, "abstract"),
            authors=authors,
            publication_year=year,
            journal=venue,
            conference=venue if venue and "conference" in venue.lower() else None,
            publisher="OpenReview",
            url=f"https://openreview.net/forum?id={note_id}" if note_id else None,
            pdf_url=_https(pdf_url),
            keywords=_content_list(content, "keywords")[:12],
            open_access=True,
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {"open_access": self.name},
            extra={"openreview_id": note_id},
        )


class ElifeProvider(ResearchProvider):
    name = "elife"
    display_name = "eLife"
    BASE = "https://api.elifesciences.org/search"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "for": query,
            "per-page": min(filters.max_results, 50),
            "page": 1,
            "type": "research-article",
        }
        data = await self.request_json(self.BASE, params=params)
        items = (data or {}).get("items") or []
        return _finished([self._parse(item) for item in items], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title = _plain(item.get("title")) or _text(item.get("title"))
        if not title:
            return None
        article_id = _text(item.get("id") or item.get("elocationId"))
        pdf = item.get("pdf") if isinstance(item.get("pdf"), dict) else {}
        pdf_url = _https(_text((pdf or {}).get("uri") or item.get("pdf")))
        if not pdf_url and article_id:
            pdf_url = f"https://elifesciences.org/articles/{article_id}.pdf"
        authors = _authors_from(item.get("authors") or item.get("authorLine"), "name", "preferredName")
        if not authors and item.get("authorLine"):
            authors = [AuthorRecord(name=n.strip()) for n in str(item.get("authorLine")).split(",") if n.strip()]
        doi = normalize_doi(_text(item.get("doi")))
        return PaperRecord(
            title=title,
            abstract=_plain(item.get("impactStatement") or item.get("abstract")),
            authors=authors,
            publication_year=_year(item.get("published") or item.get("statusDate")),
            publication_date=str(item.get("published") or "")[:10] or None,
            journal="eLife",
            publisher="eLife",
            doi=doi,
            url=f"https://elifesciences.org/articles/{article_id}" if article_id else doi_url(doi),
            pdf_url=pdf_url,
            open_access=True,
            license="CC-BY",
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {"open_access": self.name},
            extra={"elife_id": article_id},
        )


class ScipostProvider(ResearchProvider):
    name = "scipost"
    display_name = "SciPost"
    BASE = "https://scipost.org/api/publications/"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {"search": query}
        if filters.year_from:
            params["publication_date__year__gte"] = filters.year_from
        if filters.year_to:
            params["publication_date__year__lte"] = filters.year_to
        data = await self.request_json(self.BASE, params=params)
        items = data if isinstance(data, list) else (data or {}).get("results") or []
        return _finished([self._parse(item) for item in items[: filters.max_results]], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title = _text(item.get("title"))
        if not title:
            return None
        doi = normalize_doi(_text(item.get("doi")))
        doi_label = _text(item.get("doi_label"))
        authors = [AuthorRecord(name=n.strip()) for n in str(item.get("author_list") or "").split(",") if n.strip()]
        pdf_url = f"https://scipost.org/{doi_label}/pdf" if doi_label else None
        landing = _https(_text(item.get("url"))) or (f"https://scipost.org/{doi_label}" if doi_label else doi_url(doi))
        return PaperRecord(
            title=title,
            abstract=_text(item.get("abstract")),
            authors=authors,
            publication_year=_year(item.get("publication_date")),
            publication_date=str(item.get("publication_date") or "")[:10] or None,
            publisher="SciPost",
            doi=doi,
            url=landing,
            pdf_url=pdf_url,
            open_access=True,
            license=_text(item.get("cc_license")),
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {"open_access": self.name},
            extra={"doi_label": doi_label},
        )


class PaperswithcodeProvider(ResearchProvider):
    name = "paperswithcode"
    display_name = "Papers with Code"
    BASE = "https://paperswithcode.com/api/v1/papers/"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params = {"q": query, "items_per_page": min(filters.max_results, 50)}
        data = await self.request_json(self.BASE, params=params)
        items = (data or {}).get("results") or []
        return _finished([self._parse(item) for item in items], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title = _text(item.get("title"))
        if not title:
            return None
        arxiv_id = _text(item.get("arxiv_id"))
        pdf_url = _https(_text(item.get("url_pdf")))
        if not pdf_url and arxiv_id:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        return PaperRecord(
            title=title,
            abstract=_text(item.get("abstract")),
            authors=_authors_from(item.get("authors"), "full_name", "name"),
            publication_year=_year(item.get("published") or item.get("proceeding")),
            publication_date=str(item.get("published") or "")[:10] or None,
            conference=_text(item.get("proceeding")),
            publisher="Papers with Code",
            arxiv_id=arxiv_id,
            url=_https(_text(item.get("url_abs") or item.get("url"))) or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None),
            pdf_url=pdf_url,
            open_access=bool(pdf_url),
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {},
            extra={"pwc_id": item.get("id")},
        )


class ZbmathProvider(ResearchProvider):
    name = "zbmath"
    display_name = "zbMATH Open"
    BASE = "https://api.zbmath.org/v1/document/_search"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "search_string": query,
            "page": 0,
            "results_per_page": min(filters.max_results, 50),
        }
        data = await self.request_json(self.BASE, params=params)
        payload = (data or {}).get("result") or data or {}
        items = payload.get("data") or payload.get("results") or payload.get("documents") or []
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            items = data.get("data") or items
        return _finished([self._parse(item) for item in _as_list(items)], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title_node = item.get("title")
        title = _text(title_node.get("title") if isinstance(title_node, dict) else title_node)
        if not title:
            return None
        doi = None
        pdf_url = None
        for link in _as_list(item.get("links") or item.get("identifiers") or item.get("doi")):
            if isinstance(link, str):
                doi = doi or normalize_doi(link)
                continue
            if not isinstance(link, dict):
                continue
            kind = str(link.get("type") or link.get("schema") or "").lower()
            ident = _text(link.get("identifier") or link.get("url") or link.get("value"))
            if "doi" in kind or (ident or "").startswith("10."):
                doi = doi or normalize_doi(ident)
            if "pdf" in kind or _pick_pdf(ident):
                pdf_url = pdf_url or _https(ident)
        doi = doi or normalize_doi(_text(item.get("doi")))
        authors = _authors_from(
            ((item.get("contributors") or {}).get("authors") if isinstance(item.get("contributors"), dict) else None)
            or item.get("authors")
            or item.get("author"),
            "name",
            "display_name",
        )
        zb_id = _text(item.get("id") or item.get("zbmath_id") or item.get("document_id"))
        return PaperRecord(
            title=title,
            abstract=_plain(item.get("editorial_contribution") or item.get("review") or item.get("abstract")),
            authors=authors,
            publication_year=_year(item.get("year") or item.get("publication_year")),
            journal=_text((item.get("source") or {}).get("series") if isinstance(item.get("source"), dict) else item.get("journal")),
            publisher="zbMATH Open",
            doi=doi,
            url=f"https://zbmath.org/{zb_id}" if zb_id else doi_url(doi),
            pdf_url=pdf_url,
            source_provider=self.name,
            extra={"zbmath_id": zb_id},
        )


class UsgsProvider(ResearchProvider):
    name = "usgs"
    display_name = "USGS Publications"
    BASE = "https://pubs.usgs.gov/pubs-services/publication"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {"q": query, "page_size": min(filters.max_results, 50), "page_number": 1}
        if filters.year_from:
            params["startYear"] = filters.year_from
        if filters.year_to:
            params["endYear"] = filters.year_to
        data = await self.request_json(self.BASE, params=params)
        items = (data or {}).get("records") or []
        return _finished([self._parse(item) for item in items], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title = _plain(item.get("title"))
        if not title:
            return None
        doi = normalize_doi(_text(item.get("doi")))
        index_id = _text(item.get("indexId"))
        pdf_url = None
        landing = None
        for link in item.get("links") or []:
            if not isinstance(link, dict):
                continue
            href = _https(_text(link.get("url")))
            kind = str((link.get("linkFileType") or {}).get("text") or "").lower()
            type_text = str((link.get("type") or {}).get("text") or "").lower()
            if kind == "pdf" or (href or "").lower().endswith(".pdf"):
                pdf_url = href
            elif type_text in {"index page", "index"} and href:
                landing = landing or href
        authors_raw = ((item.get("contributors") or {}).get("authors")) or item.get("authors") or []
        authors: list[AuthorRecord] = []
        for author in _as_list(authors_raw):
            if isinstance(author, dict):
                given = _text(author.get("given")) or ""
                family = _text(author.get("family")) or ""
                name = f"{given} {family}".strip() or _text(author.get("text")) or ""
                if name:
                    authors.append(AuthorRecord(name=re.sub(r"\s+\S+@\S+", "", name).strip()))
        series = (item.get("seriesTitle") or {}).get("text") if isinstance(item.get("seriesTitle"), dict) else item.get("seriesTitle")
        return PaperRecord(
            title=title,
            abstract=_plain(item.get("docAbstract")),
            authors=authors,
            publication_year=_year(item.get("publicationYear")),
            journal=_text(series),
            publisher=_text(item.get("publisher")) or "U.S. Geological Survey",
            doi=doi,
            url=landing or (f"https://pubs.usgs.gov/publication/{index_id}" if index_id else doi_url(doi)),
            pdf_url=pdf_url,
            keywords=[k for k in (_text(k.get("text") if isinstance(k, dict) else k) for k in _as_list(item.get("keywords"))) if k][:12],
            open_access=bool(pdf_url),
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {},
            extra={"usgs_id": item.get("id"), "index_id": index_id},
        )


class DataverseProvider(ResearchProvider):
    name = "dataverse"
    display_name = "Harvard Dataverse"
    BASE = "https://dataverse.harvard.edu/api/search"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params = {"q": query, "type": "dataset", "per_page": min(filters.max_results, 50), "start": 0}
        data = await self.request_json(self.BASE, params=params)
        items = ((data or {}).get("data") or {}).get("items") or []
        return _finished([self._parse(item) for item in items], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title = _text(item.get("name") or item.get("title"))
        if not title:
            return None
        global_id = _text(item.get("global_id") or item.get("identifier"))
        doi = normalize_doi(global_id)
        authors = [AuthorRecord(name=t) for n in _as_list(item.get("authors") or item.get("authorsList")) if (t := _text(n))]
        if not authors:
            authors = _authors_from(item.get("authors"), "name")
        return PaperRecord(
            title=title,
            abstract=_text(item.get("description")),
            authors=authors,
            publication_year=_year(item.get("published_at") or item.get("published_date")),
            publication_date=str(item.get("published_at") or "")[:10] or None,
            publisher=_text(item.get("publisher") or item.get("citationHtml")) or "Harvard Dataverse",
            doi=doi,
            url=_https(_text(item.get("url"))) or doi_url(doi),
            keywords=[k for k in (_text(k) for k in _as_list(item.get("keywords"))) if k][:12],
            open_access=True,
            source_provider=self.name,
            metadata_sources={"open_access": self.name},
            extra={"global_id": global_id},
        )


class _Dspace7Provider(ResearchProvider):
    origin: str = ""
    publisher_name: str = ""

    @property
    def BASE(self) -> str:
        return f"{self.origin}/server/api/discover/search/objects"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params = {"query": query, "size": min(filters.max_results, 50), "dsoType": "Item"}
        data = await self.request_json(self.BASE, params=params, headers={"Accept": "application/json"})
        objects = ((((data or {}).get("_embedded") or {}).get("searchResult") or {}).get("_embedded") or {}).get("objects") or []
        return _finished([self._parse(item) for item in objects], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        obj = ((item.get("_embedded") or {}).get("indexableObject")) or item.get("indexableObject") or item
        meta = _dspace_meta(obj.get("metadata") or {})
        title = _dspace_first(meta, "dc.title") or _text(obj.get("name"))
        if not title:
            return None
        doi = normalize_doi(_dspace_first(meta, "dc.identifier.doi"))
        handle = obj.get("handle") or _dspace_first(meta, "dc.identifier.uri")
        landing = None
        if handle and not str(handle).startswith("http"):
            landing = f"{self.origin}/handle/{handle}"
        else:
            landing = _https(_text(handle))
        authors = [AuthorRecord(name=n) for n in (meta.get("dc.contributor.author") or []) if n]
        pdf_url = None
        for bitstream in ((obj.get("_embedded") or {}).get("bitstreams") or {}).get("_embedded", {}).get("bitstreams") or []:
            mime = str(bitstream.get("mimeType") or "").lower()
            name = str(bitstream.get("name") or "")
            href = ((bitstream.get("_links") or {}).get("content") or {}).get("href")
            if mime == "application/pdf" or name.lower().endswith(".pdf"):
                pdf_url = _https(_text(href))
                break
        return PaperRecord(
            title=title,
            abstract=_dspace_first(meta, "dc.description.abstract", "dc.description"),
            authors=authors,
            publication_year=_year(_dspace_first(meta, "dc.date.issued", "dc.date.available")),
            publisher=_dspace_first(meta, "dc.publisher") or self.publisher_name,
            doi=doi,
            url=landing or doi_url(doi),
            pdf_url=pdf_url,
            keywords=list(meta.get("dc.subject") or [])[:12],
            open_access=True,
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {"open_access": self.name},
        )


class FaoProvider(_Dspace7Provider):
    name = "fao"
    display_name = "FAO Knowledge Repository"
    origin = "https://openknowledge.fao.org"
    publisher_name = "FAO"


class WhoProvider(_Dspace7Provider):
    name = "who"
    display_name = "WHO IRIS"
    origin = "https://iris.who.int"
    publisher_name = "World Health Organization"


class CernProvider(ResearchProvider):
    name = "cern"
    display_name = "CERN CDS"
    BASE = "https://repository.cern/api/records"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params = {"q": query, "size": min(filters.max_results, 25)}
        data = await self.request_json(self.BASE, params=params)
        hits: list[Any] = []
        if isinstance(data, dict):
            nested = data.get("hits")
            if isinstance(nested, dict):
                hits = list(nested.get("hits") or [])
        return _finished([self._parse(item) for item in hits], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        meta = item.get("metadata") or item
        title = _text(meta.get("title"))
        if not title:
            return None
        doi = normalize_doi(
            _text(meta.get("doi") or (item.get("pids") or {}).get("doi", {}).get("identifier"))
        )
        if not doi:
            for ident in _as_list(meta.get("identifiers")):
                if isinstance(ident, dict) and str(ident.get("scheme") or "").lower() == "doi":
                    doi = normalize_doi(_text(ident.get("identifier")))
                    if doi:
                        break
        pdf_url = None
        files = item.get("files") or {}
        entries = files.get("entries") if isinstance(files, dict) else files
        for blob in _as_list(entries.values() if isinstance(entries, dict) else entries):
            if not isinstance(blob, dict):
                continue
            key = str(blob.get("key") or blob.get("filename") or "")
            mime = str(blob.get("mimetype") or "").lower()
            links = blob.get("links") if isinstance(blob.get("links"), dict) else {}
            href = _text((links or {}).get("content") or (links or {}).get("self"))
            if mime == "application/pdf" or key.lower().endswith(".pdf"):
                pdf_url = _https(href)
                if pdf_url:
                    break
        links = item.get("links") or {}
        html = _https(_text(links.get("self_html") or links.get("html")))
        creators = meta.get("creators") or []
        authors = _authors_from(
            [c.get("person_or_org") if isinstance(c, dict) and c.get("person_or_org") else c for c in _as_list(creators)],
            "name",
        )
        return PaperRecord(
            title=title,
            abstract=_plain(meta.get("description") or ((meta.get("additional_descriptions") or [{}])[0].get("description") if meta.get("additional_descriptions") else None)),
            authors=authors,
            publication_year=_year(meta.get("publication_date") or meta.get("publicationDate")),
            publication_date=str(meta.get("publication_date") or "")[:10] or None,
            publisher="CERN",
            doi=doi,
            url=html or doi_url(doi),
            pdf_url=pdf_url,
            keywords=[k for k in (_text(k.get("subject") if isinstance(k, dict) else k) for k in _as_list(meta.get("subjects"))) if k][:12],
            open_access=True,
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {"open_access": self.name},
            extra={"cern_id": item.get("id")},
        )


class NdlProvider(ResearchProvider):
    name = "ndl"
    display_name = "NDL Search"
    BASE = "https://ndlsearch.ndl.go.jp/api/opensearch"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "any": query,
            "cnt": min(filters.max_results, 50),
            "mediatype": "articles",
        }
        if filters.year_from:
            params["from"] = str(filters.year_from)
        if filters.year_to:
            params["until"] = str(filters.year_to)
        xml_text = await self.request_text(self.BASE, params=params)
        return _finished(self._parse_rss(xml_text), filters)

    def _parse_rss(self, xml_text: str) -> list[PaperRecord | None]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []
        papers: list[PaperRecord | None] = []
        for item in root.findall(".//item"):
            papers.append(self._parse_item(item))
        return papers

    def _parse_item(self, item: ET.Element) -> PaperRecord | None:
        title = _text("".join(item.findtext("title") or "").strip())
        if not title:
            return None
        creators = [c.text.strip() for c in item.findall("dc:creator", _DC_NS) if c.text and c.text.strip()]
        if not creators:
            creators = [c.text.strip() for c in item.findall("{http://purl.org/dc/elements/1.1/}creator") if c is not None and c.text]
        date_text = (
            item.findtext("dc:date", default="", namespaces=_DC_NS)
            or item.findtext("{http://purl.org/dc/elements/1.1/}date")
            or ""
        )
        doi = None
        for ident in item.findall("dc:identifier", _DC_NS) + item.findall("{http://purl.org/dc/elements/1.1/}identifier"):
            doi = doi or normalize_doi(ident.text)
        landing = _https((item.findtext("link") or "").strip() or None)
        return PaperRecord(
            title=title,
            abstract=_plain(item.findtext("description")),
            authors=[AuthorRecord(name=n) for n in creators],
            publication_year=_year(date_text),
            publication_date=str(date_text)[:10] or None,
            publisher=_text(item.findtext("dc:publisher", default="", namespaces=_DC_NS)) or "National Diet Library",
            doi=doi,
            url=landing or doi_url(doi),
            source_provider=self.name,
        )
