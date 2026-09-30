"""Science and technology ebook providers built from EBOOK_SOURCES specs."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.database.ebook_sources import EBOOK_SOURCES
from app.models.paper import AuthorRecord, PaperRecord
from app.models.search import SearchFilters
from app.providers.base import ResearchProvider
from app.providers.extra import NasaNtrsProvider
from app.providers.free import (
    _authors_from,
    _DspaceRestProvider,
    _finished,
    _https,
    _pick_pdf,
    _text,
    _year,
)
from app.providers.openalex import OpenAlexProvider
from app.utils.pdf_url import is_direct_pdf_url


def _dspace_cover(origin: str, item: dict[str, Any]) -> str | None:
    for bitstream in item.get("bitstreams") or []:
        if not isinstance(bitstream, dict):
            continue
        mime = str(bitstream.get("mimeType") or bitstream.get("mimetype") or "").lower()
        name = str(bitstream.get("name") or "").lower()
        bundle = str(bitstream.get("bundleName") or bitstream.get("bundle") or "").lower()
        link = bitstream.get("retrieveLink") or bitstream.get("link") or ""
        is_image = mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
        is_thumb = "thumb" in bundle or "thumb" in name or "cover" in name
        if not (is_image or is_thumb):
            continue
        if isinstance(link, str) and link.startswith("http"):
            return _https(link)
        if isinstance(link, str) and link.startswith("/"):
            return f"{origin}{link}"
    return None


def _stamp_ebook(paper: PaperRecord | None, spec: dict[str, object]) -> PaperRecord | None:
    if paper is None:
        return None
    paper.work_type = "ebook"
    paper.category = str(spec.get("category") or paper.category or "")
    fields = list(paper.research_fields or [])
    family = str(spec.get("family") or "").title()
    if paper.category and paper.category not in fields:
        fields.insert(0, paper.category)
    if family and family not in fields:
        fields.append(family)
    paper.research_fields = fields
    paper.open_access = True if paper.open_access is None else paper.open_access
    return paper


class _DoabSubjectProvider(_DspaceRestProvider):
    content_kind = "ebook"
    origin = "https://directory.doabooks.org"
    publisher_name = "DOAB"
    upstream = "doab"
    rate_group = "doab"
    subject: str = ""
    category: str = ""
    family: str = "science"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        subject = self.subject.strip()
        combined = f"({query}) AND ({subject})" if subject else query
        data = await self.request_json(
            f"{self.origin}/rest/search",
            params={"query": combined, "expand": "metadata,bitstreams"},
        )
        items = data if isinstance(data, list) else []
        spec = {"category": self.category, "family": self.family}
        papers = []
        for item in items[: filters.max_results]:
            paper = self._parse(item)
            if paper:
                paper.cover_url = paper.cover_url or _dspace_cover(self.origin, item)
            papers.append(_stamp_ebook(paper, spec))
        return _finished(papers, filters)


class _OapenSubjectProvider(_DoabSubjectProvider):
    origin = "https://library.oapen.org"
    publisher_name = "OAPEN"
    upstream = "oapen"
    rate_group = "oapen"


_OPENALEX_BOOK_TYPES = frozenset(
    {"book", "book-chapter", "edited-book", "monograph", "reference-book"}
)


def _openalex_is_book(item: dict[str, Any] | None) -> bool:
    if not isinstance(item, dict):
        return False
    work_type = str(item.get("type") or item.get("type_crossref") or "").strip().lower()
    if work_type in _OPENALEX_BOOK_TYPES:
        return True
    return "book" in work_type and "article" not in work_type


class _OpenAlexBookProvider(OpenAlexProvider):
    content_kind = "ebook"
    upstream = "openalex"
    rate_group = "openalex"
    concept_id: str = ""
    query_extra: str = ""
    category: str = ""
    family: str = "science"

    def _search_params(self, query: str, filters: SearchFilters) -> dict[str, Any]:
        params = super()._search_params(query, filters)
        extra = self.query_extra.strip()
        if extra and extra.lower() not in query.lower():
            params["search"] = f"{query} {extra}"
        filt = [
            part
            for part in str(params.get("filter") or "").split(",")
            if part and not part.startswith("type:")
        ]
        filt.append("type:book")
        if "is_oa:true" not in filt:
            filt.append("is_oa:true")
        if self.concept_id:
            filt.append(f"concepts.id:{self.concept_id}")
        params["filter"] = ",".join(filt)
        return params

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params = self._search_params(query, filters)
        data = await self.request_json(self.BASE, params=params)
        results = (data or {}).get("results") or []
        spec = {"category": self.category, "family": self.family}
        papers = []
        for item in results:
            if not _openalex_is_book(item):
                continue
            papers.append(_stamp_ebook(self._parse(item), spec))
        return [paper for paper in papers if paper]


class _InternetArchiveBooksProvider(ResearchProvider):
    content_kind = "ebook"
    upstream = "archive"
    rate_group = "archive"
    BASE = "https://archive.org/advancedsearch.php"
    META = "https://archive.org/metadata"
    ia_clause: str = 'mediatype:texts'
    category: str = ""
    family: str = "science"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        quoted = query.replace('"', " ").strip()
        clause = self.ia_clause or 'mediatype:texts'
        q = f'mediatype:texts AND ({clause}) AND (title:({quoted}) OR description:({quoted}) OR subject:({quoted}))'
        params: dict[str, Any] = {
            "q": q,
            "fl[]": ["identifier", "title", "creator", "year", "description", "licenseurl", "publicdate"],
            "rows": min(filters.max_results, 50),
            "page": 1,
            "output": "json",
        }
        data = await self.request_json(self.BASE, params=params)
        docs = (((data or {}).get("response") or {}).get("docs")) or []
        spec = {"category": self.category, "family": self.family}
        return _finished([_stamp_ebook(self._parse(doc), spec) for doc in docs], filters)

    async def find_pdf(self, paper: PaperRecord) -> str | None:
        if paper.pdf_url and is_direct_pdf_url(paper.pdf_url, prefer_https=False):
            return paper.pdf_url
        ident = (paper.extra or {}).get("archive_id")
        if not ident:
            return None
        data = await self.request_json(f"{self.META}/{ident}")
        files = (data or {}).get("files") or []
        for blob in files:
            if not isinstance(blob, dict):
                continue
            name = str(blob.get("name") or "")
            fmt = str(blob.get("format") or "").lower()
            if name.lower().endswith(".pdf") or "pdf" in fmt:
                return _https(f"https://archive.org/download/{ident}/{quote(name)}")
        return None

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title = _text(item.get("title"))
        ident = _text(item.get("identifier"))
        if not title or not ident:
            return None
        creators = item.get("creator")
        year = _year(item.get("year") or item.get("publicdate"))
        landing = f"https://archive.org/details/{ident}"
        return PaperRecord(
            title=title,
            abstract=_text(item.get("description")),
            authors=_authors_from(creators) if creators else [],
            publication_year=year,
            publisher="Internet Archive",
            url=landing,
            pdf_url=None,
            cover_url=f"https://archive.org/services/img/{ident}",
            open_access=True,
            license=_text(item.get("licenseurl")),
            source_provider=self.name,
            metadata_sources={"open_access": self.name, "cover_url": self.name},
            extra={"archive_id": ident},
            work_type="ebook",
        )


class _OpenStaxProvider(ResearchProvider):
    content_kind = "ebook"
    upstream = "openstax"
    rate_group = "openstax"
    BASE = "https://openstax.org/apps/cms/api/books/"
    category: str = "Technology"
    family: str = "technology"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        data = await self.request_json(self.BASE, params={"format": "json"})
        books = data if isinstance(data, list) else (data or {}).get("books") or (data or {}).get("items") or []
        needle = query.strip().lower()
        spec = {"category": self.category, "family": self.family}
        parsed: list[PaperRecord | None] = []
        for item in books:
            paper = self._parse(item)
            if not paper:
                continue
            hay = " ".join(
                part
                for part in (paper.title, paper.abstract, " ".join(paper.keywords), paper.category)
                if part
            ).lower()
            if needle and needle not in hay:
                continue
            parsed.append(_stamp_ebook(paper, spec))
        return _finished(parsed[: filters.max_results], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title = _text(item.get("title") or item.get("book_title") or item.get("name"))
        if not title:
            return None
        authors_raw = item.get("authors") or item.get("book_authors") or []
        authors: list[AuthorRecord] = []
        for row in authors_raw if isinstance(authors_raw, list) else [authors_raw]:
            if isinstance(row, dict):
                name = _text(row.get("name") or row.get("value"))
            else:
                name = _text(row)
            if name:
                authors.append(AuthorRecord(name=name))
        pdf = _pick_pdf(
            item.get("high_resolution_pdf_url"),
            item.get("low_resolution_pdf_url"),
            item.get("webview_rex_link"),
            item.get("cover_url"),
        )
        cover = _https(_text(item.get("cover_url") or item.get("cover_url_large")))
        year = _year(item.get("publish_date") or item.get("created") or item.get("updated"))
        slug = _text(item.get("slug"))
        if not slug and isinstance(item.get("meta"), dict):
            slug = _text(item["meta"].get("slug"))
        if not slug:
            slug = _text(item.get("id"))
        landing = _https(_text(item.get("webview_rex_link") or item.get("book_url")))
        if not landing and slug:
            landing = f"https://openstax.org/details/books/{slug}"
        return PaperRecord(
            title=title,
            abstract=_text(item.get("description") or item.get("blurb")),
            authors=authors,
            publication_year=year,
            publisher="OpenStax",
            url=landing,
            pdf_url=pdf if pdf and str(pdf).lower().endswith(".pdf") else None,
            cover_url=cover,
            open_access=True,
            license=_text(item.get("license_name") or "CC BY"),
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name, "cover_url": self.name} if pdf else {"open_access": self.name},
            work_type="ebook",
        )


class _NasaNtrsBookProvider(NasaNtrsProvider):
    content_kind = "ebook"
    upstream = "nasa_ntrs"
    rate_group = "nasa_ntrs"
    query_extra: str = ""
    category: str = "Satellite Technology"
    family: str = "science"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        extra = self.query_extra.strip()
        combined = f"{query} {extra}".strip()
        papers = await super().search(combined, filters)
        spec = {"category": self.category, "family": self.family}
        return [_stamp_ebook(paper, spec) for paper in papers if paper]


def _class_name(slug: str) -> str:
    return "".join(part.title() for part in slug.replace("-", "_").split("_")) + "Provider"


def _build_provider(spec: dict[str, object]) -> type[ResearchProvider]:
    backend = str(spec.get("backend") or "")
    attrs: dict[str, object] = {
        "name": str(spec["slug"]),
        "display_name": str(spec["display_name"]),
        "category": str(spec.get("category") or ""),
        "family": str(spec.get("family") or "science"),
        "content_kind": "ebook",
    }
    if backend == "doab":
        attrs.update({"subject": str(spec.get("subject") or ""), "upstream": "doab", "rate_group": "doab"})
        return type(_class_name(str(spec["slug"])), (_DoabSubjectProvider,), attrs)
    if backend == "oapen":
        attrs.update({"subject": str(spec.get("subject") or ""), "upstream": "oapen", "rate_group": "oapen"})
        return type(_class_name(str(spec["slug"])), (_OapenSubjectProvider,), attrs)
    if backend == "openalex":
        attrs.update(
            {
                "concept_id": str(spec.get("concept_id") or ""),
                "query_extra": str(spec.get("query_extra") or ""),
                "upstream": "openalex",
                "rate_group": "openalex",
            }
        )
        return type(_class_name(str(spec["slug"])), (_OpenAlexBookProvider,), attrs)
    if backend == "archive":
        attrs.update(
            {
                "ia_clause": str(spec.get("ia_clause") or "mediatype:texts"),
                "upstream": "archive",
                "rate_group": "archive",
            }
        )
        return type(_class_name(str(spec["slug"])), (_InternetArchiveBooksProvider,), attrs)
    if backend == "openstax":
        return type(_class_name(str(spec["slug"])), (_OpenStaxProvider,), attrs)
    if backend == "nasa_ntrs":
        attrs.update(
            {
                "query_extra": str(spec.get("query_extra") or ""),
                "upstream": "nasa_ntrs",
                "rate_group": "nasa_ntrs",
            }
        )
        return type(_class_name(str(spec["slug"])), (_NasaNtrsBookProvider,), attrs)
    raise ValueError(f"Unknown ebook backend: {backend}")


EBOOK_PROVIDER_CLASSES: list[type[ResearchProvider]] = [_build_provider(spec) for spec in EBOOK_SOURCES]
