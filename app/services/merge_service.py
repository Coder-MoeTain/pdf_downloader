"""Merge paper records from multiple providers into one canonical record."""

from __future__ import annotations

from collections.abc import Iterable

from app.models.paper import AuthorRecord, PaperRecord
from app.utils.doi import normalize_doi

FIELD_PRIORITY: dict[str, list[str]] = {
    "doi": ["crossref", "openalex", "elsevier", "springer", "ieee", "europe_pmc", "pubmed", "semantic_scholar"],
    "publisher": ["crossref", "springer", "elsevier", "ieee", "openalex"],
    "journal": ["crossref", "pubmed", "europe_pmc", "openalex", "springer"],
    "abstract": ["semantic_scholar", "openalex", "europe_pmc", "pubmed", "arxiv", "core", "doaj", "nasa_ntrs", "nasa_ads", "hal", "inspire", "eric", "osti", "elife", "scipost", "openreview", "usgs", "fao", "who"],
    "citation_count": ["semantic_scholar", "openalex", "crossref", "nasa_ads", "elsevier", "inspire"],
    "pdf_url": ["unpaywall", "arxiv", "europe_pmc", "pubmed", "pmc", "openalex", "semantic_scholar", "core", "doaj", "nasa_ntrs", "plos", "zenodo", "hal", "osti", "inspire", "biorxiv", "medrxiv", "osf", "fatcat", "elife", "peerj", "scipost", "openreview", "chemrxiv", "usgs", "cern"],
}


def _rank(field: str, provider: str | None) -> int:
    order = FIELD_PRIORITY.get(field, [])
    try:
        return order.index(provider or "")
    except ValueError:
        return 100


def _pick_text(field: str, records: list[PaperRecord], getter) -> tuple[str | None, str | None]:
    best_value: str | None = None
    best_source: str | None = None
    best_rank = 999
    for rec in records:
        value = getter(rec)
        if not value:
            continue
        rank = _rank(field, rec.source_provider)
        if best_value is None or rank < best_rank or (rank == best_rank and len(str(value)) > len(str(best_value))):
            best_value = value
            best_source = rec.source_provider
            best_rank = rank
    return best_value, best_source


def merge_authors(groups: list[list[AuthorRecord]]) -> list[AuthorRecord]:
    if not groups:
        return []
    best = max(groups, key=lambda g: (len(g), sum(len(a.affiliations) for a in g)))
    merged: list[AuthorRecord] = []
    seen: set[str] = set()
    for author in best:
        key = author.name.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(author)
    return merged


def merge_papers(records: Iterable[PaperRecord]) -> PaperRecord:
    items = [r for r in records if r]
    if not items:
        raise ValueError("Cannot merge an empty record list")

    sources: dict[str, str] = {}
    extra: dict = {}
    providers: list[str] = []
    author_groups: list[list[AuthorRecord]] = []
    keywords: list[str] = []
    fields: list[str] = []

    title, title_src = _pick_text("title", items, lambda r: r.title)
    abstract, abs_src = _pick_text("abstract", items, lambda r: r.abstract)
    journal, journal_src = _pick_text("journal", items, lambda r: r.journal)
    publisher, pub_src = _pick_text("publisher", items, lambda r: r.publisher)
    if title_src:
        sources["title"] = title_src
    if abs_src:
        sources["abstract"] = abs_src
    if journal_src:
        sources["journal"] = journal_src
    if pub_src:
        sources["publisher"] = pub_src

    doi = None
    doi_rank = 999
    pdf_url = None
    pdf_rank = 999
    year = None
    pub_date = None
    conference = None
    volume = issue = pages = None
    pmid = pmcid = arxiv_id = openalex_id = semantic_scholar_id = None
    url = None
    citations: int | None = None
    refs: int | None = None
    open_access: bool | None = None
    license_ = None

    for rec in items:
        extra.update(rec.extra or {})
        if rec.source_provider and rec.source_provider not in providers:
            providers.append(rec.source_provider)
        if rec.authors:
            author_groups.append(rec.authors)
        incoming_doi = normalize_doi(rec.doi)
        rank = _rank("doi", rec.source_provider)
        if incoming_doi and (doi is None or rank < doi_rank):
            doi, doi_rank = incoming_doi, rank
            sources["doi"] = rec.source_provider
        rank = _rank("pdf_url", rec.source_provider)
        if rec.pdf_url and (pdf_url is None or rank < pdf_rank):
            pdf_url, pdf_rank = rec.pdf_url, rank
            sources["pdf_url"] = rec.source_provider
        year = year or rec.publication_year
        pub_date = pub_date or rec.publication_date
        conference = conference or rec.conference
        volume = volume or rec.volume
        issue = issue or rec.issue
        pages = pages or rec.pages
        pmid = pmid or rec.pmid
        pmcid = pmcid or rec.pmcid
        arxiv_id = arxiv_id or rec.arxiv_id
        openalex_id = openalex_id or rec.openalex_id
        semantic_scholar_id = semantic_scholar_id or rec.semantic_scholar_id
        url = url or rec.url
        if rec.citation_count is not None:
            citations = rec.citation_count if citations is None else max(citations, rec.citation_count)
            sources.setdefault("citation_count", rec.source_provider)
        if rec.reference_count is not None:
            refs = rec.reference_count if refs is None else max(refs, rec.reference_count)
        for kw in rec.keywords or []:
            if kw and kw not in keywords:
                keywords.append(kw)
        for field in rec.research_fields or []:
            if field and field not in fields:
                fields.append(field)
        if rec.open_access is True:
            open_access = True
            sources["open_access"] = rec.source_provider
        elif open_access is None and rec.open_access is False:
            open_access = False
        license_ = license_ or rec.license
        for key, val in (rec.metadata_sources or {}).items():
            sources.setdefault(key, val)

    return PaperRecord(
        title=title or items[0].title,
        abstract=abstract,
        authors=merge_authors(author_groups),
        publication_year=year,
        publication_date=pub_date,
        journal=journal,
        conference=conference,
        volume=volume,
        issue=issue,
        pages=pages,
        publisher=publisher,
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        arxiv_id=arxiv_id,
        openalex_id=openalex_id,
        semantic_scholar_id=semantic_scholar_id,
        url=url,
        pdf_url=pdf_url,
        citation_count=citations,
        reference_count=refs,
        keywords=keywords,
        research_fields=fields,
        open_access=open_access,
        license=license_,
        source_provider="+".join(providers),
        metadata_sources=sources,
        extra=extra,
    )
