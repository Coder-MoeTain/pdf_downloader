"""Free, no-key academic APIs. Official HTTP endpoints only; PDFs when marked OA."""

from __future__ import annotations

from typing import Any

from app.models.paper import AuthorRecord, PaperRecord
from app.models.search import SearchFilters
from app.providers.base import ResearchProvider
from app.providers.crossref import CrossrefProvider
from app.utils.doi import doi_url, normalize_doi


def _year(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value)
    digits = "".join(ch for ch in text[:4] if ch.isdigit())
    return int(digits) if len(digits) == 4 else None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            text = _text(item)
            if text:
                return text
        return None
    if isinstance(value, dict):
        for key in ("value", "$", "#text", "title", "name", "label", "fullName", "full_name"):
            if value.get(key):
                return _text(value.get(key))
        return None
    text = str(value).strip()
    return text or None


def _dig(item: dict[str, Any], *names: str) -> Any:
    lower = {k.lower(): v for k, v in item.items()}
    for name in names:
        if name in item and item[name] not in (None, "", []):
            return item[name]
        found = lower.get(name.lower())
        if found not in (None, "", []):
            return found
    return None


def _is_pdf_url(url: str | None) -> bool:
    if not url:
        return False
    lower = url.lower()
    return (
        ".pdf" in lower
        or "/pdf/" in lower
        or lower.endswith("/pdf")
        or "type=printable" in lower
        or "type=pdf" in lower
        or "format=pdf" in lower
        or "script=sci_pdf" in lower
        or "/download" in lower
        or "servlets/purl" in lower
    )


def _pick_pdf(*candidates: Any) -> str | None:
    for value in candidates:
        for item in _as_list(value):
            url = _text(item) if not isinstance(item, str) else item.strip()
            if _is_pdf_url(url):
                return url.replace("http://", "https://", 1) if url.startswith("http://") else url
    return None


def _https(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("http://"):
        return "https://" + url[7:]
    return url


def _authors_from(values: Any, *name_keys: str) -> list[AuthorRecord]:
    keys = name_keys or ("name", "fullName", "full_name", "author", "text")
    authors: list[AuthorRecord] = []
    for item in _as_list(values):
        if isinstance(item, str):
            name = item.strip()
            if name:
                authors.append(AuthorRecord(name=name))
            continue
        if not isinstance(item, dict):
            continue
        name = ""
        for key in keys:
            name = _text(item.get(key)) or ""
            if name:
                break
        if not name:
            given = _text(item.get("given") or item.get("first_name") or item.get("givenName")) or ""
            family = _text(item.get("family") or item.get("surname") or item.get("last_name")) or ""
            name = f"{given} {family}".strip()
        if not name:
            continue
        aff = item.get("affiliation") or item.get("affiliationName")
        affiliations = [a for a in (_text(a) for a in _as_list(aff)) if a]
        authors.append(AuthorRecord(name=name, affiliations=affiliations, orcid=_text(item.get("orcid") or item.get("ORCID"))))
    return authors


def _finished(papers: list[PaperRecord | None], filters: SearchFilters) -> list[PaperRecord]:
    out = [p for p in papers if p and p.title]
    if filters.year_from:
        out = [p for p in out if not p.publication_year or p.publication_year >= filters.year_from]
    if filters.year_to:
        out = [p for p in out if not p.publication_year or p.publication_year <= filters.year_to]
    return out


def _dspace_meta(raw: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or "")
            val = _text(row.get("value"))
            if key and val:
                out.setdefault(key, []).append(val)
    elif isinstance(raw, dict):
        for key, vals in raw.items():
            for item in _as_list(vals):
                val = _text(item.get("value") if isinstance(item, dict) else item)
                if key and val:
                    out.setdefault(str(key), []).append(val)
    return out


def _dspace_first(meta: dict[str, list[str]], *keys: str) -> str | None:
    for key in keys:
        values = meta.get(key) or []
        if values:
            return values[0]
    return None


def _dspace_pdf(origin: str, item: dict[str, Any]) -> str | None:
    for bitstream in item.get("bitstreams") or []:
        if not isinstance(bitstream, dict):
            continue
        mime = str(bitstream.get("mimeType") or bitstream.get("mimetype") or "").lower()
        name = str(bitstream.get("name") or "")
        link = bitstream.get("retrieveLink") or bitstream.get("link") or ""
        if mime != "application/pdf" and not name.lower().endswith(".pdf"):
            continue
        if isinstance(link, str) and link.startswith("http"):
            return _https(link)
        if isinstance(link, str) and link.startswith("/"):
            return f"{origin}{link}"
    return None


def _is_open(value: Any) -> bool:
    text = " ".join(str(v) for v in _as_list(value) if v).lower()
    if isinstance(value, dict):
        text = " ".join(str(v) for v in value.values() if v).lower()
    return "open" in text and "closed" not in text


class OpenaireProvider(ResearchProvider):
    name = "openaire"
    display_name = "OpenAIRE"
    BASE = "https://api.openaire.eu/graph/v3/research-products"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "search": query,
            "type": "publication",
            "pageSize": min(filters.max_results, 50),
            "page": 1,
            "sortBy": "relevance DESC",
        }
        data = await self.request_json(self.BASE, params=params)
        items = (data or {}).get("results") or []
        return _finished([self._parse(item) for item in items], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title = _text(_dig(item, "mainTitle", "maintitle", "title"))
        if not title:
            return None
        authors = _authors_from(_dig(item, "authors", "author"), "fullName", "full_name", "name")
        year = _year(_dig(item, "publicationYear", "publicationyear", "publicationDate", "publicationdate"))
        pub_date = _text(_dig(item, "publicationDate", "publicationdate"))
        pids = _as_list(_dig(item, "pids", "pid"))
        doi = None
        for pid in pids:
            if not isinstance(pid, dict):
                doi = doi or normalize_doi(str(pid))
                continue
            scheme = str(pid.get("scheme") or pid.get("classid") or "").lower()
            value = _text(pid.get("value") or pid.get("$"))
            if "doi" in scheme or (value and value.lower().startswith("10.")):
                doi = normalize_doi(value)
                if doi:
                    break
        instances = _as_list(_dig(item, "instances", "instance"))
        urls: list[str] = []
        for inst in instances:
            if not isinstance(inst, dict):
                continue
            urls.extend(_text(u) or "" for u in _as_list(inst.get("urls") or inst.get("url")))
        urls = [u for u in urls if u]
        pdf_url = _pick_pdf(*urls)
        access = _dig(item, "bestAccessRight", "bestaccessright")
        oa = _is_open(access) or bool(pdf_url)
        container = _dig(item, "container", "journal")
        journal = _text(container) if not isinstance(container, dict) else _text(container.get("name") or container.get("title"))
        subjects = [_text(s) for s in _as_list(_dig(item, "subjects", "subject")) if _text(s)]
        descriptions = _as_list(_dig(item, "descriptions", "description"))
        cites = _dig(item, "indicators") or {}
        if isinstance(cites, dict):
            cites = ((cites.get("citationImpact") or {}).get("citationCount") if isinstance(cites.get("citationImpact"), dict) else cites.get("citationCount"))
        return PaperRecord(
            title=title,
            abstract=_text(descriptions[0] if descriptions else None),
            authors=authors,
            publication_year=year,
            publication_date=(pub_date or "")[:10] or None,
            journal=journal,
            publisher=_text(_dig(item, "publisher", "publishers")),
            doi=doi,
            url=urls[0] if urls else doi_url(doi),
            pdf_url=pdf_url,
            citation_count=int(cites) if str(cites or "").isdigit() else None,
            keywords=[s for s in subjects if s],
            open_access=oa,
            source_provider=self.name,
            metadata_sources={"open_access": self.name},
        )


class HalProvider(ResearchProvider):
    name = "hal"
    display_name = "HAL"
    BASE = "https://api.archives-ouvertes.fr/search/"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        q = query
        if filters.year_from or filters.year_to:
            start = filters.year_from or 1900
            end = filters.year_to or 2100
            q = f"({query}) AND producedDateY_i:[{start} TO {end}]"
        params = {
            "q": q,
            "wt": "json",
            "rows": min(filters.max_results, 50),
            "fl": "title_s,abstract_s,doiId_s,uri_s,fileMain_s,authFullName_s,producedDateY_i,journalTitle_s,keyword_s,licence_s,submittedDate_s,docType_s",
        }
        data = await self.request_json(self.BASE, params=params)
        docs = ((data or {}).get("response") or {}).get("docs") or []
        return _finished([self._parse(item) for item in docs], filters)

    def _parse(self, item: dict[str, Any]) -> PaperRecord | None:
        title = _text(item.get("title_s"))
        if not title:
            return None
        doi = normalize_doi(_text(item.get("doiId_s")))
        pdf_url = _https(_text(item.get("fileMain_s")))
        return PaperRecord(
            title=title,
            abstract=_text(item.get("abstract_s")),
            authors=_authors_from(item.get("authFullName_s")),
            publication_year=_year(item.get("producedDateY_i") or item.get("submittedDate_s")),
            publication_date=str(item.get("submittedDate_s") or "")[:10] or None,
            journal=_text(item.get("journalTitle_s")),
            publisher="HAL",
            doi=doi,
            url=_https(_text(item.get("uri_s"))) or doi_url(doi),
            pdf_url=pdf_url if pdf_url else None,
            keywords=[k for k in (_text(k) for k in _as_list(item.get("keyword_s"))) if k],
            open_access=True,
            license=_text(item.get("licence_s")),
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {"open_access": self.name},
        )


class ZenodoProvider(ResearchProvider):
    name = "zenodo"
    display_name = "Zenodo"
    BASE = "https://zenodo.org/api/records"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        # Anonymous Zenodo search rejects size > 25 ("A validation error occurred.").
        params: dict[str, Any] = {
            "q": query,
            "size": min(filters.max_results, 25),
            "type": "publication",
        }
        data = await self.request_json(self.BASE, params=params)
        hits: list[Any] = []
        if isinstance(data, dict):
            nested = data.get("hits")
            if isinstance(nested, dict):
                hits = list(nested.get("hits") or [])
            elif isinstance(nested, list):
                hits = nested
        return _finished([self._parse(item) for item in hits], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        meta = item.get("metadata") or item
        title = _text(meta.get("title"))
        if not title:
            return None
        doi = normalize_doi(_text(meta.get("doi") or item.get("doi") or (item.get("pids") or {}).get("doi", {}).get("identifier")))
        files = item.get("files") or meta.get("files") or []
        pdf_url = None
        for blob in files:
            if not isinstance(blob, dict):
                continue
            key = str(blob.get("key") or blob.get("filename") or blob.get("name") or "")
            mime = str(blob.get("mimetype") or blob.get("type") or "").lower()
            links = blob.get("links") or {}
            href = _text(links.get("self") or links.get("content") or links.get("download") or blob.get("url"))
            if mime == "application/pdf" or key.lower().endswith(".pdf") or str(blob.get("type") or "") == "pdf":
                pdf_url = _https(href)
                if pdf_url:
                    break
        links = item.get("links") or {}
        html = _https(_text(links.get("html") or links.get("self_html") or meta.get("url")))
        license_ = meta.get("license")
        license_text = _text(license_.get("id") if isinstance(license_, dict) else license_)
        journal = meta.get("journal") if isinstance(meta.get("journal"), dict) else None
        return PaperRecord(
            title=title,
            abstract=_text(meta.get("description")),
            authors=_authors_from(meta.get("creators") or meta.get("creators_list"), "name"),
            publication_year=_year(meta.get("publication_date") or meta.get("publicationDate")),
            publication_date=str(meta.get("publication_date") or "")[:10] or None,
            journal=_text((journal or {}).get("title")) if journal else None,
            publisher="Zenodo",
            doi=doi,
            url=html or doi_url(doi),
            pdf_url=pdf_url,
            keywords=[k for k in (_text(k) for k in _as_list(meta.get("keywords"))) if k],
            open_access=True,
            license=license_text,
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {"open_access": self.name},
            extra={"zenodo_id": item.get("id")},
        )


class DblpProvider(ResearchProvider):
    name = "dblp"
    display_name = "DBLP"
    BASE = "https://dblp.org/search/publ/api"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params = {"q": query, "format": "json", "h": min(filters.max_results, 50)}
        data = await self.request_json(self.BASE, params=params)
        hits = ((((data or {}).get("result") or {}).get("hits") or {}).get("hit")) or []
        return _finished([self._parse(item) for item in _as_list(hits)], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        info = (item or {}).get("info") or item or {}
        title = _text(info.get("title"))
        if not title:
            return None
        doi = normalize_doi(_text(info.get("doi")))
        authors_raw = info.get("authors") or {}
        author_nodes = authors_raw.get("author") if isinstance(authors_raw, dict) else authors_raw
        ee = _https(_text(info.get("ee")))
        landing = _https(_text(info.get("url")))
        if landing and landing.startswith("https://dblp.org/rec/") and not landing.endswith(".html"):
            landing = landing
        return PaperRecord(
            title=title,
            authors=_authors_from(author_nodes, "text", "name"),
            publication_year=_year(info.get("year")),
            journal=_text(info.get("venue")),
            conference=_text(info.get("venue")) if _text(info.get("type")) in {"inproceedings", "conference"} else None,
            publisher="DBLP",
            doi=doi,
            url=ee or landing or doi_url(doi),
            pdf_url=_pick_pdf(ee),
            open_access=bool(_pick_pdf(ee)),
            source_provider=self.name,
            metadata_sources={"doi": self.name},
        )


class PlosProvider(ResearchProvider):
    name = "plos"
    display_name = "PLOS"
    BASE = "https://api.plos.org/search"
    _SLUGS = {
        "plos one": "plosone",
        "plos biology": "plosbiology",
        "plos medicine": "plosmedicine",
        "plos computational biology": "ploscompbiol",
        "plos genetics": "plosgenetics",
        "plos pathogens": "plospathogens",
        "plos neglected tropical diseases": "plosntds",
        "plos digital health": "digitalhealth",
        "plos climate": "climate",
        "plos water": "water",
        "plos global public health": "globalpublichealth",
    }

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "q": query,
            "wt": "json",
            "rows": min(filters.max_results, 50),
            "fl": "id,title,author,abstract,publication_date,journal,doi,article_type",
        }
        data = await self.request_json(self.BASE, params=params)
        docs = ((data or {}).get("response") or {}).get("docs") or []
        return _finished([self._parse(item) for item in docs], filters)

    def _parse(self, item: dict[str, Any]) -> PaperRecord | None:
        title = _text(item.get("title"))
        if not title:
            return None
        doi = normalize_doi(_text(item.get("doi") or item.get("id")))
        journal = _text(item.get("journal"))
        slug = self._SLUGS.get((journal or "").lower())
        pdf_url = None
        if doi and slug:
            pdf_url = f"https://journals.plos.org/{slug}/article/file?id={doi}&type=printable"
        return PaperRecord(
            title=title,
            abstract=_text(item.get("abstract")),
            authors=_authors_from(item.get("author")),
            publication_year=_year(item.get("publication_date")),
            publication_date=str(item.get("publication_date") or "")[:10] or None,
            journal=journal,
            publisher="PLOS",
            doi=doi,
            url=f"https://doi.org/{doi}" if doi else None,
            pdf_url=pdf_url,
            open_access=True,
            license="CC BY",
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {"open_access": self.name},
        )


class EricProvider(ResearchProvider):
    name = "eric"
    display_name = "ERIC"
    BASE = "https://api.ies.ed.gov/eric/"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "search": query,
            "format": "json",
            "rows": min(max(filters.max_results, 20), 200),
        }
        if filters.year_from:
            params["publicationdatestart"] = f"{filters.year_from}-01-01"
        if filters.year_to:
            params["publicationdateend"] = f"{filters.year_to}-12-31"
        data = await self.request_json(self.BASE, params=params)
        docs = (data or {}).get("docs") or ((data or {}).get("response") or {}).get("docs") or []
        if isinstance(data, dict) and isinstance(data.get("response"), dict):
            docs = data["response"].get("docs") or docs
        return _finished([self._parse(item) for item in docs], filters)

    def _parse(self, item: dict[str, Any]) -> PaperRecord | None:
        title = _text(item.get("title"))
        if not title:
            return None
        urls = [u for u in (_text(u) for u in _as_list(item.get("url") or item.get("urltext"))) if u]
        pdf_url = _pick_pdf(*urls)
        doi = normalize_doi(_text(item.get("doi") or item.get("idnumber")))
        return PaperRecord(
            title=title,
            abstract=_text(item.get("description") or item.get("abstract")),
            authors=_authors_from(item.get("author") or item.get("authors")),
            publication_year=_year(item.get("publicationdateyear") or item.get("publicationdate")),
            journal=_text(item.get("source") or item.get("journal")),
            publisher=_text(item.get("publisher")) or "ERIC",
            doi=doi,
            url=_https(urls[0] if urls else None) or doi_url(doi),
            pdf_url=pdf_url,
            keywords=[k for k in (_text(k) for k in _as_list(item.get("subject") or item.get("descriptor"))) if k],
            open_access=bool(pdf_url),
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {"abstract": self.name},
            extra={"eric_id": item.get("id") or item.get("idnumber")},
        )


class OstiProvider(ResearchProvider):
    name = "osti"
    display_name = "DOE OSTI"
    BASE = "https://www.osti.gov/api/v1/records"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {"q": query, "rows": min(filters.max_results, 50)}
        if filters.year_from:
            params["publication_date_start"] = f"01/01/{filters.year_from}"
        if filters.year_to:
            params["publication_date_end"] = f"12/31/{filters.year_to}"
        data = await self.request_json(
            self.BASE,
            params=params,
            headers={"Accept": "application/json"},
        )
        items = data if isinstance(data, list) else (data or {}).get("records") or (data or {}).get("results") or []
        return _finished([self._parse(item) for item in items], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title = _text(item.get("title"))
        if not title:
            return None
        osti_id = item.get("osti_id") or item.get("id")
        doi = normalize_doi(_text(item.get("doi")))
        authors_raw = item.get("authors") or item.get("author")
        if authors_raw and isinstance(authors_raw, list) and authors_raw and isinstance(authors_raw[0], dict):
            authors = _authors_from(authors_raw, "full_name", "name")
        else:
            authors = _authors_from(authors_raw)
        links = item.get("links") or []
        hrefs = []
        if isinstance(links, dict):
            hrefs = [links.get("pdf") or links.get("fulltext") or links.get("citation")]
        else:
            for link in _as_list(links):
                if isinstance(link, dict):
                    hrefs.append(link.get("href") or link.get("url") or link.get("rel"))
                else:
                    hrefs.append(link)
        has_fulltext = str(item.get("has_fulltext") or item.get("fulltext") or "").lower() in {"true", "1", "yes"}
        pdf_url = _pick_pdf(*hrefs)
        if not pdf_url and has_fulltext and osti_id:
            pdf_url = f"https://www.osti.gov/servlets/purl/{osti_id}"
        return PaperRecord(
            title=title,
            abstract=_text(item.get("description") or item.get("abstract")),
            authors=authors,
            publication_year=_year(item.get("publication_date") or item.get("publication_year")),
            publication_date=str(item.get("publication_date") or "")[:10] or None,
            journal=_text(item.get("journal_name") or item.get("journal")),
            publisher=_text(item.get("publisher")) or "U.S. Department of Energy",
            doi=doi,
            url=_https(_text(item.get("citation_url"))) or (f"https://www.osti.gov/biblio/{osti_id}" if osti_id else doi_url(doi)),
            pdf_url=pdf_url,
            keywords=[k for k in (_text(k) for k in _as_list(item.get("keywords") or item.get("subject"))) if k],
            open_access=bool(pdf_url) or has_fulltext,
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {"abstract": self.name},
            extra={"osti_id": osti_id},
        )


class DataciteProvider(ResearchProvider):
    name = "datacite"
    display_name = "DataCite"
    BASE = "https://api.datacite.org/dois"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "query": query,
            "resource-type-id": "Text",
            "page[size]": min(filters.max_results, 50),
        }
        data = await self.request_json(self.BASE, params=params)
        items = (data or {}).get("data") or []
        return _finished([self._parse(item) for item in items], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        attrs = item.get("attributes") or item
        titles = attrs.get("titles") or []
        title = None
        if titles and isinstance(titles[0], dict):
            title = _text(titles[0].get("title"))
        else:
            title = _text(titles)
        if not title:
            return None
        doi = normalize_doi(_text(item.get("id") or attrs.get("doi")))
        descriptions = attrs.get("descriptions") or []
        abstract = None
        if descriptions and isinstance(descriptions[0], dict):
            abstract = _text(descriptions[0].get("description"))
        rights = attrs.get("rightsList") or []
        license_ = _text((rights[0] or {}).get("rights") if rights and isinstance(rights[0], dict) else None)
        oa = bool(license_) or "open" in str(attrs.get("types") or "").lower()
        return PaperRecord(
            title=title,
            abstract=abstract,
            authors=_authors_from(attrs.get("creators"), "name"),
            publication_year=_year(attrs.get("publicationYear") or attrs.get("published")),
            journal=_text((attrs.get("container") or {}).get("title") if isinstance(attrs.get("container"), dict) else None),
            publisher=_text(attrs.get("publisher")),
            doi=doi,
            url=_https(_text(attrs.get("url"))) or doi_url(doi),
            pdf_url=_pick_pdf(attrs.get("url"), attrs.get("contentUrl")),
            keywords=[_text(s.get("subject") if isinstance(s, dict) else s) or "" for s in (attrs.get("subjects") or [])],
            open_access=oa or bool(_pick_pdf(attrs.get("url"))),
            license=license_,
            source_provider=self.name,
            metadata_sources={"doi": self.name},
        )


class OsfProvider(ResearchProvider):
    name = "osf"
    display_name = "OSF Preprints"
    BASE = "https://api.osf.io/v2/preprints/"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "filter[title]": query,
            "page[size]": min(filters.max_results, 50),
        }
        data = await self.request_json(self.BASE, params=params)
        items = (data or {}).get("data") or []
        return _finished([self._parse(item) for item in items], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        attrs = item.get("attributes") or {}
        title = _text(attrs.get("title"))
        if not title:
            return None
        doi = normalize_doi(_text(attrs.get("doi") or attrs.get("preprint_doi")))
        links = item.get("links") or {}
        html = _https(_text(links.get("html") or links.get("self")))
        pdf_url = _https(_text(links.get("download")))
        embed = ((item.get("embeds") or {}).get("primary_file") or {}).get("data") or {}
        if not pdf_url and isinstance(embed, dict):
            file_links = embed.get("links") or {}
            pdf_url = _https(_text(file_links.get("download")))
        ident = item.get("id")
        return PaperRecord(
            title=title,
            abstract=_text(attrs.get("description") or attrs.get("abstract")),
            publication_year=_year(attrs.get("date_published") or attrs.get("date_created")),
            publication_date=str(attrs.get("date_published") or attrs.get("date_created") or "")[:10] or None,
            publisher="OSF",
            doi=doi,
            url=html or (f"https://osf.io/{ident}" if ident else doi_url(doi)),
            pdf_url=pdf_url,
            keywords=[k for k in (_text(k) for k in _as_list(attrs.get("tags") or attrs.get("subjects"))) if k],
            open_access=True,
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {"open_access": self.name},
            extra={"osf_id": ident},
        )


class _RxivProvider(ResearchProvider):
    """bioRxiv / medRxiv via Crossref container filter, then landing URL on the preprint server."""

    container: str = ""
    host: str = ""

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        filter_parts = [f"container-title:{self.container}", "type:posted-content"]
        if filters.year_from:
            filter_parts.append(f"from-pub-date:{filters.year_from}")
        if filters.year_to:
            filter_parts.append(f"until-pub-date:{filters.year_to}")
        params: dict[str, Any] = {
            "query": query,
            "filter": ",".join(filter_parts),
            "rows": min(filters.max_results, 50),
            "mailto": self.config.env.polite_email,
        }
        data = await self.request_json("https://api.crossref.org/works", params=params)
        items = (((data or {}).get("message") or {}).get("items")) or []
        parser = CrossrefProvider(self.client, self.config)
        papers: list[PaperRecord | None] = []
        for item in items:
            paper = parser._parse(item)
            if not paper:
                continue
            paper.source_provider = self.name
            paper.open_access = True
            paper.journal = paper.journal or self.container
            paper.publisher = self.container
            if paper.doi:
                paper.url = f"{self.host}/content/{paper.doi}"
            papers.append(paper)
        return _finished(papers, filters)


class BiorxivProvider(_RxivProvider):
    name = "biorxiv"
    display_name = "bioRxiv"
    container = "bioRxiv"
    host = "https://www.biorxiv.org"


class MedrxivProvider(_RxivProvider):
    name = "medrxiv"
    display_name = "medRxiv"
    container = "medRxiv"
    host = "https://www.medrxiv.org"


class FigshareProvider(ResearchProvider):
    name = "figshare"
    display_name = "Figshare"
    BASE = "https://api.figshare.com/v2/articles"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params = {"search_for": query, "page_size": min(filters.max_results, 50), "order": "published_date", "order_direction": "desc"}
        data = await self.request_json(self.BASE, params=params)
        items = data if isinstance(data, list) else (data or {}).get("items") or []
        return _finished([self._parse(item) for item in items], filters)

    async def find_pdf(self, paper: PaperRecord) -> str | None:
        if paper.pdf_url:
            return paper.pdf_url
        fid = (paper.extra or {}).get("figshare_id")
        if not fid:
            return None
        data = await self.request_json(f"{self.BASE}/{fid}")
        for blob in (data or {}).get("files") or []:
            name = str(blob.get("name") or "")
            mime = str(blob.get("mimetype") or "").lower()
            href = blob.get("download_url") or blob.get("url")
            if mime == "application/pdf" or name.lower().endswith(".pdf"):
                return _https(_text(href))
        return None

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title = _text(item.get("title"))
        if not title:
            return None
        doi = normalize_doi(_text(item.get("doi")))
        return PaperRecord(
            title=title,
            abstract=_text(item.get("description")),
            authors=_authors_from(item.get("authors"), "full_name", "name"),
            publication_year=_year(item.get("published_date") or item.get("created_date")),
            publication_date=str(item.get("published_date") or "")[:10] or None,
            publisher="Figshare",
            doi=doi,
            url=_https(_text(item.get("url_public_html") or item.get("url"))) or doi_url(doi),
            pdf_url=_pick_pdf(item.get("url")),
            keywords=[k for k in (_text(k) for k in _as_list(item.get("tags") or item.get("categories"))) if k],
            open_access=True,
            license=_text((item.get("license") or {}).get("name") if isinstance(item.get("license"), dict) else item.get("license")),
            source_provider=self.name,
            metadata_sources={"open_access": self.name},
            extra={"figshare_id": item.get("id")},
        )


class _DspaceRestProvider(ResearchProvider):
    origin: str = ""
    publisher_name: str = ""

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        data = await self.request_json(
            f"{self.origin}/rest/search",
            params={"query": query, "expand": "metadata,bitstreams"},
        )
        items = data if isinstance(data, list) else []
        return _finished([self._parse(item) for item in items[: filters.max_results]], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        meta = _dspace_meta(item.get("metadata") or [])
        title = _dspace_first(meta, "dc.title", "dc.title.alternative") or _text(item.get("name"))
        if not title:
            return None
        doi = normalize_doi(_dspace_first(meta, "dc.identifier.doi", "dc.identifier.uri"))
        handle = item.get("handle")
        landing = f"{self.origin}/handle/{handle}" if handle else _dspace_first(meta, "dc.identifier.uri")
        authors = [AuthorRecord(name=n) for n in (meta.get("dc.contributor.author") or meta.get("dc.creator") or []) if n]
        return PaperRecord(
            title=title,
            abstract=_dspace_first(meta, "dc.description.abstract", "dc.description"),
            authors=authors,
            publication_year=_year(_dspace_first(meta, "dc.date.issued", "dc.date.available", "dc.date")),
            publication_date=(_dspace_first(meta, "dc.date.issued") or "")[:10] or None,
            publisher=_dspace_first(meta, "dc.publisher") or self.publisher_name,
            doi=doi,
            url=_https(landing) or doi_url(doi),
            pdf_url=_dspace_pdf(self.origin, item),
            keywords=list(meta.get("dc.subject") or [])[:12],
            open_access=True,
            license=_dspace_first(meta, "dc.rights", "dc.rights.uri"),
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if _dspace_pdf(self.origin, item) else {"open_access": self.name},
        )


class DoabProvider(_DspaceRestProvider):
    name = "doab"
    display_name = "DOAB"
    origin = "https://directory.doabooks.org"
    publisher_name = "DOAB"


class OapenProvider(_DspaceRestProvider):
    name = "oapen"
    display_name = "OAPEN"
    origin = "https://library.oapen.org"
    publisher_name = "OAPEN"


class EconstorProvider(_DspaceRestProvider):
    name = "econstor"
    display_name = "EconStor"
    origin = "https://www.econstor.eu"
    publisher_name = "EconStor"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        # DSpace 6.4: /rest/search is gone; filtered-items is the public keyword search.
        limit = min(filters.max_results, 50)
        data = await self.request_json(
            f"{self.origin}/rest/filtered-items",
            params={
                "query_field[]": ["dc.title", "dc.description.abstract"],
                "query_op[]": ["contains", "contains"],
                "query_val[]": [query, query],
                "limit": limit,
                "offset": 0,
                "expand": "metadata,bitstreams",
            },
            headers={"Accept": "application/json"},
        )
        items = data if isinstance(data, list) else (data or {}).get("items") or []
        return _finished([self._parse(item) for item in items[:limit]], filters)


class ScieloProvider(ResearchProvider):
    name = "scielo"
    display_name = "SciELO"
    BASE = "https://search.scielo.org/"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {"q": query, "lang": "en", "count": min(filters.max_results, 50), "output": "json"}
        data = await self.request_json(self.BASE, params=params)
        docs = (data or {}).get("docs") or ((data or {}).get("response") or {}).get("docs") or []
        return _finished([self._parse(item) for item in docs], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title = _text(item.get("ti") or item.get("title"))
        if not title:
            return None
        doi = normalize_doi(_text(item.get("doi") or item.get("DOI")))
        urls = [u for u in (_text(u) for u in _as_list(item.get("ur") or item.get("url") or item.get("ur_html"))) if u]
        landing = _https(urls[0] if urls else None)
        pdf_url = _pick_pdf(*urls)
        if not pdf_url and landing and "script=sci_arttext" in landing:
            pdf_url = landing.replace("script=sci_arttext", "script=sci_pdf")
        return PaperRecord(
            title=title,
            abstract=_text(item.get("ab") or item.get("abstract")),
            authors=_authors_from(item.get("au") or item.get("author")),
            publication_year=_year(item.get("year") or item.get("da") or item.get("publication_year")),
            journal=_text(item.get("ta") or item.get("journal")),
            publisher="SciELO",
            doi=doi,
            url=landing or doi_url(doi),
            pdf_url=pdf_url,
            open_access=True,
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {"open_access": self.name},
        )


class CiniiProvider(ResearchProvider):
    name = "cinii"
    display_name = "CiNii"
    BASE = "https://cir.nii.ac.jp/opensearch/articles"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params = {"q": query, "count": min(filters.max_results, 50), "format": "json", "lang": "en"}
        data = await self.request_json(self.BASE, params=params)
        items = (data or {}).get("items") or []
        if not items:
            graph = _as_list((data or {}).get("@graph"))
            for node in graph:
                if isinstance(node, dict) and node.get("items"):
                    items = node["items"]
                    break
                if isinstance(node, dict) and (_text(node.get("title")) and node.get("@id")):
                    items.append(node)
        return _finished([self._parse(item) for item in items], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title = _text(item.get("title") or item.get("dc:title"))
        if not title or title.lower().startswith("cinii"):
            return None
        doi = normalize_doi(_text(item.get("prism:doi") or item.get("doi")))
        link = item.get("link")
        landing = _https(_text(link.get("@id") if isinstance(link, dict) else link) or item.get("@id"))
        return PaperRecord(
            title=title,
            abstract=_text(item.get("description") or item.get("dc:description")),
            authors=_authors_from(item.get("dc:creator") or item.get("creator") or item.get("author")),
            publication_year=_year(item.get("dc:date") or item.get("prism:publicationDate") or item.get("date")),
            journal=_text(item.get("prism:publicationName") or item.get("publicationName")),
            publisher=_text(item.get("dc:publisher")) or "CiNii",
            doi=doi,
            url=landing or doi_url(doi),
            pdf_url=_pick_pdf(landing),
            open_access=bool(doi),
            source_provider=self.name,
            metadata_sources={"doi": self.name},
        )


class InspireProvider(ResearchProvider):
    name = "inspire"
    display_name = "INSPIRE-HEP"
    BASE = "https://inspirehep.net/api/literature"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params: dict[str, Any] = {"q": query, "size": min(filters.max_results, 50)}
        data = await self.request_json(self.BASE, params=params)
        hits = ((data or {}).get("hits") or {}).get("hits") or []
        return _finished([self._parse(item) for item in hits], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        meta = item.get("metadata") or item
        titles = meta.get("titles") or []
        title = _text((titles[0] or {}).get("title") if titles else None)
        if not title:
            return None
        dois = meta.get("dois") or []
        doi = normalize_doi(_text((dois[0] or {}).get("value") if dois else None))
        arxivs = meta.get("arxiv_eprints") or []
        arxiv_id = _text((arxivs[0] or {}).get("value") if arxivs else None)
        abstracts = meta.get("abstracts") or []
        abstract = _text((abstracts[0] or {}).get("value") if abstracts else None)
        pubs = meta.get("publication_info") or []
        journal = _text((pubs[0] or {}).get("journal_title") if pubs else None)
        pdf_url = None
        for doc in meta.get("documents") or []:
            if not isinstance(doc, dict) or doc.get("hidden"):
                continue
            href = _text(doc.get("url"))
            key = str(doc.get("key") or "")
            if _is_pdf_url(href) or key.lower().endswith(".pdf"):
                pdf_url = _https(href)
                break
        if not pdf_url and arxiv_id:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        authors = _authors_from(meta.get("authors"), "full_name", "first_name")
        rec_id = item.get("id") or meta.get("control_number")
        return PaperRecord(
            title=title,
            abstract=abstract,
            authors=authors,
            publication_year=_year(meta.get("earliest_date") or (pubs[0] or {}).get("year") if pubs else None),
            publication_date=str(meta.get("earliest_date") or "")[:10] or None,
            journal=journal,
            publisher="INSPIRE-HEP",
            doi=doi,
            arxiv_id=arxiv_id,
            url=f"https://inspirehep.net/literature/{rec_id}" if rec_id else doi_url(doi),
            pdf_url=pdf_url,
            citation_count=meta.get("citation_count"),
            open_access=bool(pdf_url or arxiv_id),
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {"abstract": self.name},
        )


class FatcatProvider(ResearchProvider):
    name = "fatcat"
    display_name = "Fatcat / IA Scholar"
    BASE = "https://api.fatcat.wiki/v0/release/search"

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        params = {"q": query, "limit": min(filters.max_results, 50)}
        data = await self.request_json(self.BASE, params=params)
        items = (data or {}).get("releases") or (data or {}).get("results") or []
        return _finished([self._parse(item) for item in items], filters)

    def _parse(self, item: dict[str, Any] | None) -> PaperRecord | None:
        if not item:
            return None
        title = _text(item.get("title"))
        if not title:
            return None
        ext = item.get("ext_ids") or {}
        doi = normalize_doi(_text(ext.get("doi") or item.get("doi")))
        pdf_url = None
        for blob in item.get("files") or []:
            mime = str(blob.get("mimetype") or "").lower()
            for link in blob.get("urls") or []:
                href = _text(link.get("url") if isinstance(link, dict) else link)
                rel = str((link.get("rel") if isinstance(link, dict) else "") or "").lower()
                if mime == "application/pdf" or _is_pdf_url(href) or rel in {"webarchive", "repository", "pdf"}:
                    pdf_url = _https(href)
                    if pdf_url:
                        break
            if pdf_url:
                break
        container = item.get("container") or {}
        return PaperRecord(
            title=title,
            authors=_authors_from(item.get("contribs") or item.get("contrib_names"), "raw_name", "surname"),
            publication_year=_year(item.get("release_year") or item.get("release_date")),
            publication_date=str(item.get("release_date") or "")[:10] or None,
            journal=_text(container.get("name") if isinstance(container, dict) else container),
            publisher=_text(container.get("publisher") if isinstance(container, dict) else None),
            doi=doi,
            pmid=_text(ext.get("pmid")),
            pmcid=_text(ext.get("pmcid")),
            arxiv_id=_text(ext.get("arxiv")),
            url=doi_url(doi) or (f"https://fatcat.wiki/release/{item.get('ident')}" if item.get("ident") else None),
            pdf_url=pdf_url,
            open_access=bool(pdf_url),
            source_provider=self.name,
            metadata_sources={"pdf_url": self.name} if pdf_url else {"doi": self.name},
        )


class WorldbankProvider(ResearchProvider):
    name = "worldbank"
    display_name = "World Bank OKR"
    BASE = "https://openknowledge.worldbank.org/server/api/discover/search/objects"

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
            landing = f"https://openknowledge.worldbank.org/handle/{handle}"
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
            publisher=_dspace_first(meta, "dc.publisher") or "World Bank",
            doi=doi,
            url=landing or doi_url(doi),
            pdf_url=pdf_url,
            keywords=list(meta.get("dc.subject") or [])[:12],
            open_access=True,
            source_provider=self.name,
            metadata_sources={"open_access": self.name},
        )
