"""Repository helpers for papers, searches, and downloads."""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from copy import deepcopy

from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.database.models import Author, CrawlJob, Download, Paper, PaperAuthor, PaperFulltext, Provider, SearchJob, SearchQuery, SearchResult, User
from app.models.paper import AuthorRecord, PaperRecord, PaperStatus
from app.utils.filename import normalize_title
from app.utils.time import utc_now

_FACETS_TTL_SECONDS = 120.0
_DASHBOARD_TTL_SECONDS = 30.0
_facets_cache: dict[str, dict] = {}
_facets_cache_at: dict[str, float] = {}
_dashboard_cache: dict | None = None
_dashboard_cache_at: float = 0.0
_facets_lock = threading.Lock()


def invalidate_library_facets_cache() -> None:
    global _dashboard_cache, _dashboard_cache_at
    with _facets_lock:
        _facets_cache.clear()
        _facets_cache_at.clear()
        _dashboard_cache = None
        _dashboard_cache_at = 0.0


def _join(values: list[str] | None) -> str | None:
    if not values:
        return None
    return "; ".join(v for v in values if v)


def upsert_provider(session: Session, name: str, *, error: str | None = None) -> Provider:
    provider = session.scalar(select(Provider).where(Provider.name == name))
    if provider is None:
        provider = Provider(name=name, enabled=True, request_count=0)
        session.add(provider)
    provider.last_used = utc_now()
    provider.request_count = (provider.request_count or 0) + 1
    provider.last_error = error
    return provider


def get_or_create_author(session: Session, record: AuthorRecord) -> Author:
    norm = normalize_title(record.name)
    author = session.scalar(select(Author).where(Author.normalized_name == norm))
    if author is None:
        author = Author(
            name=record.name.strip(),
            normalized_name=norm,
            affiliations=_join(record.affiliations),
            orcid=record.orcid,
        )
        session.add(author)
        session.flush()
    else:
        if record.affiliations and not author.affiliations:
            author.affiliations = _join(record.affiliations)
        if record.orcid and not author.orcid:
            author.orcid = record.orcid
    return author


def paper_to_record(paper: Paper) -> PaperRecord:
    authors = [
        AuthorRecord(
            name=link.author.name,
            affiliations=[a.strip() for a in (link.author.affiliations or "").split(";") if a.strip()],
            orcid=link.author.orcid,
        )
        for link in sorted(paper.authors, key=lambda item: item.position)
        if link.author
    ]
    try:
        status = PaperStatus(paper.status)
    except ValueError:
        status = PaperStatus.FOUND
    return PaperRecord(
        title=paper.title,
        abstract=paper.abstract,
        authors=authors,
        publication_year=paper.publication_year,
        publication_date=paper.publication_date,
        journal=paper.journal,
        conference=paper.conference,
        volume=paper.volume,
        issue=paper.issue,
        pages=paper.pages,
        publisher=paper.publisher,
        doi=paper.doi,
        pmid=paper.pmid,
        pmcid=paper.pmcid,
        arxiv_id=paper.arxiv_id,
        openalex_id=paper.openalex_id,
        semantic_scholar_id=paper.semantic_scholar_id,
        url=paper.url,
        pdf_url=paper.pdf_url,
        citation_count=paper.citation_count,
        reference_count=paper.reference_count,
        keywords=[k.strip() for k in (paper.keywords or "").split(";") if k.strip()],
        research_fields=[k.strip() for k in (paper.research_fields or "").split(";") if k.strip()],
        open_access=paper.open_access,
        license=paper.license,
        source_provider=paper.source or "",
        metadata_sources=json.loads(paper.metadata_sources) if paper.metadata_sources else {},
        relevance_score=paper.relevance_score or 0.0,
        status=status,
    )


def save_paper(session: Session, record: PaperRecord) -> Paper:
    existing = find_existing_paper(session, record)
    if existing is None:
        paper = Paper(title=record.title)
        session.add(paper)
    else:
        paper = existing

    paper.title = record.title or paper.title
    paper.normalized_title = normalize_title(record.title)
    paper.abstract = record.abstract or paper.abstract
    paper.doi = record.doi or paper.doi
    paper.pmid = record.pmid or paper.pmid
    paper.pmcid = record.pmcid or paper.pmcid
    paper.arxiv_id = record.arxiv_id or paper.arxiv_id
    paper.openalex_id = record.openalex_id or paper.openalex_id
    paper.semantic_scholar_id = record.semantic_scholar_id or paper.semantic_scholar_id
    paper.publication_year = record.publication_year or paper.publication_year
    paper.publication_date = record.publication_date or paper.publication_date
    paper.journal = record.journal or paper.journal
    paper.conference = record.conference or paper.conference
    paper.volume = record.volume or paper.volume
    paper.issue = record.issue or paper.issue
    paper.pages = record.pages or paper.pages
    paper.publisher = record.publisher or paper.publisher
    if record.citation_count is not None:
        paper.citation_count = max(paper.citation_count or 0, record.citation_count)
    if record.reference_count is not None:
        paper.reference_count = max(paper.reference_count or 0, record.reference_count)
    paper.keywords = _join(record.keywords) or paper.keywords
    paper.research_fields = _join(record.research_fields) or paper.research_fields
    paper.url = record.url or paper.url
    paper.pdf_url = record.pdf_url or paper.pdf_url
    if record.open_access is not None:
        paper.open_access = record.open_access
    paper.license = record.license or paper.license
    paper.source = record.source_provider or paper.source
    paper.metadata_sources = json.dumps(record.metadata_sources or {})
    paper.relevance_score = record.relevance_score
    paper.status = record.status.value
    paper.updated_at = utc_now()
    session.flush()

    if record.authors:
        session.query(PaperAuthor).filter(PaperAuthor.paper_id == paper.id).delete()
        for position, author_rec in enumerate(record.authors):
            author = get_or_create_author(session, author_rec)
            session.add(PaperAuthor(paper_id=paper.id, author_id=author.id, position=position))
    return paper


def find_existing_paper(session: Session, record: PaperRecord) -> Paper | None:
    if record.doi:
        found = session.scalar(select(Paper).where(Paper.doi == record.doi))
        if found:
            return found
    if record.pmid:
        found = session.scalar(select(Paper).where(Paper.pmid == record.pmid))
        if found:
            return found
    if record.arxiv_id:
        found = session.scalar(select(Paper).where(Paper.arxiv_id == record.arxiv_id))
        if found:
            return found
    if record.openalex_id:
        found = session.scalar(select(Paper).where(Paper.openalex_id == record.openalex_id))
        if found:
            return found
    norm = normalize_title(record.title)
    if norm:
        found = session.scalar(select(Paper).where(Paper.normalized_title == norm))
        if found:
            return found
    return None


def filter_new_paper_records(session: Session, records: list[PaperRecord]) -> list[PaperRecord]:
    """Return records not already in the library, using a few bulk lookups."""
    if not records:
        return []

    dois = {str(r.doi).strip() for r in records if r.doi}
    pmids = {str(r.pmid).strip() for r in records if r.pmid}
    arxiv_ids = {str(r.arxiv_id).strip() for r in records if r.arxiv_id}
    openalex_ids = {str(r.openalex_id).strip() for r in records if r.openalex_id}
    titles = {normalize_title(r.title) for r in records if r.title}
    titles.discard("")

    existing_dois: set[str] = set()
    existing_pmids: set[str] = set()
    existing_arxiv: set[str] = set()
    existing_openalex: set[str] = set()
    existing_titles: set[str] = set()

    if dois:
        existing_dois = set(session.scalars(select(Paper.doi).where(Paper.doi.in_(dois))).all())
    if pmids:
        existing_pmids = set(session.scalars(select(Paper.pmid).where(Paper.pmid.in_(pmids))).all())
    if arxiv_ids:
        existing_arxiv = set(
            session.scalars(select(Paper.arxiv_id).where(Paper.arxiv_id.in_(arxiv_ids))).all()
        )
    if openalex_ids:
        existing_openalex = set(
            session.scalars(select(Paper.openalex_id).where(Paper.openalex_id.in_(openalex_ids))).all()
        )
    if titles:
        existing_titles = set(
            session.scalars(select(Paper.normalized_title).where(Paper.normalized_title.in_(titles))).all()
        )

    fresh: list[PaperRecord] = []
    for record in records:
        if record.doi and str(record.doi).strip() in existing_dois:
            continue
        if record.pmid and str(record.pmid).strip() in existing_pmids:
            continue
        if record.arxiv_id and str(record.arxiv_id).strip() in existing_arxiv:
            continue
        if record.openalex_id and str(record.openalex_id).strip() in existing_openalex:
            continue
        norm = normalize_title(record.title)
        if norm and norm in existing_titles:
            continue
        fresh.append(record)
    return fresh


def create_search_query(
    session: Session,
    original: str,
    expanded: list[str],
    filters: dict,
    *,
    user_id: int | None = None,
) -> SearchQuery:
    row = SearchQuery(
        original_query=original,
        expanded_queries=json.dumps(expanded),
        filters_json=json.dumps(filters),
        status="running",
        user_id=user_id,
    )
    session.add(row)
    session.flush()
    return row


def complete_search_query(session: Session, search_id: int, status: str = "completed") -> None:
    row = session.get(SearchQuery, search_id)
    if row:
        row.status = status
        row.completed_at = utc_now()


def attach_search_result(session: Session, search_id: int, paper_id: int, rank: int, score: float) -> None:
    existing = session.scalar(
        select(SearchResult).where(
            SearchResult.search_query_id == search_id,
            SearchResult.paper_id == paper_id,
        )
    )
    if existing:
        existing.rank = rank
        existing.relevance_score = score
        return
    session.add(
        SearchResult(search_query_id=search_id, paper_id=paper_id, rank=rank, relevance_score=score)
    )


def upsert_download(
    session: Session,
    paper_id: int,
    *,
    pdf_url: str | None,
    status: str,
    local_path: str | None = None,
    file_size: int | None = None,
    sha256: str | None = None,
    error: str | None = None,
    increment_retry: bool = False,
    user_id: int | None = None,
) -> Download:
    row = session.scalar(select(Download).where(Download.paper_id == paper_id).order_by(Download.id.desc()))
    if row is None:
        row = Download(paper_id=paper_id)
        session.add(row)
    row.pdf_url = pdf_url or row.pdf_url
    row.status = status
    if local_path:
        row.local_path = local_path
    if file_size is not None:
        row.file_size = file_size
    if sha256:
        row.sha256 = sha256
    if error is not None:
        row.error_message = error
    if increment_retry:
        row.retry_count = (row.retry_count or 0) + 1
    if status == PaperStatus.DOWNLOADED.value:
        row.downloaded_at = utc_now()
        row.error_message = None
    if user_id and status in {PaperStatus.DOWNLOADED.value, PaperStatus.DUPLICATE.value}:
        if row.downloaded_by_user_id is None:
            row.downloaded_by_user_id = user_id
    session.flush()
    return row


def mark_downloading_stopped(
    session: Session,
    *,
    paper_id: int | None = None,
    error: str = "Stopped by user",
) -> int:
    """Mark DOWNLOADING rows (and matching papers) as FAILED. Returns count updated."""
    stmt = select(Download).where(Download.status == PaperStatus.DOWNLOADING.value)
    if paper_id is not None:
        stmt = stmt.where(Download.paper_id == paper_id)
    rows = list(session.scalars(stmt).all())
    if not rows:
        return 0
    paper_ids = {row.paper_id for row in rows}
    for row in rows:
        row.status = PaperStatus.FAILED.value
        row.error_message = error
        row.retry_count = (row.retry_count or 0) + 1
    papers = list(session.scalars(select(Paper).where(Paper.id.in_(paper_ids))).all())
    for paper in papers:
        if paper.status in {
            PaperStatus.DOWNLOADING.value,
            PaperStatus.OA_AVAILABLE.value,
            PaperStatus.FOUND.value,
            PaperStatus.FAILED.value,
        }:
            paper.status = PaperStatus.FAILED.value
    session.flush()
    return len(rows)


def find_downloaded_by_sha256(
    session: Session,
    digest: str,
    *,
    exclude_paper_id: int | None = None,
) -> Download | None:
    """Return an earlier download of the same PDF bytes, if a local file was stored."""
    digest = (digest or "").strip().lower()
    if not digest:
        return None
    stmt = (
        select(Download)
        .where(
            Download.sha256 == digest,
            Download.local_path.is_not(None),
            Download.local_path != "",
            Download.status.in_([PaperStatus.DOWNLOADED.value, PaperStatus.DUPLICATE.value]),
        )
        .order_by(Download.id)
    )
    if exclude_paper_id is not None:
        stmt = stmt.where(Download.paper_id != exclude_paper_id)
    return session.scalar(stmt)


def list_failed_downloads(session: Session) -> list[Download]:
    return list(
        session.scalars(
            select(Download)
            .options(selectinload(Download.paper).selectinload(Paper.authors).selectinload(PaperAuthor.author))
            .where(Download.status.in_(["FAILED", "DOWNLOADING"]))
        ).all()
    )


def downloadable_clause():
    """Papers with a PDF file recorded as saved on this server."""
    return exists().where(
        Download.paper_id == Paper.id,
        Download.status.in_(
            [
                PaperStatus.DOWNLOADED.value,
                PaperStatus.DUPLICATE.value,
            ]
        ),
        Download.local_path.is_not(None),
        Download.local_path != "",
    )


def open_access_clause():
    """Papers marked open access or currently available as OA metadata."""
    return or_(
        Paper.open_access.is_(True),
        Paper.status == PaperStatus.OA_AVAILABLE.value,
    )


def show_paywalled_papers() -> bool:
    """True when paywalled records should appear in library lists and analytics."""
    try:
        from app.config import get_runtime_config

        return bool(get_runtime_config().show_paywalled)
    except Exception:
        return True


LIBRARY_HIDDEN_STATUSES = frozenset(
    {
        PaperStatus.NO_PDF.value,
        PaperStatus.FAILED.value,
        PaperStatus.SKIPPED.value,
    }
)


def visible_paper_clauses(*, status: str = "") -> tuple:
    """Omit no-PDF, failed, skipped, and paywalled papers unless that status is requested."""
    selected = status.strip().upper()
    clauses = []
    if selected not in LIBRARY_HIDDEN_STATUSES:
        clauses.append(Paper.status.notin_(LIBRARY_HIDDEN_STATUSES))
    if not show_paywalled_papers() and selected != PaperStatus.PAYWALLED.value:
        clauses.append(Paper.status != PaperStatus.PAYWALLED.value)
    return tuple(clauses)


def visible_download_clauses(*, status: str = "") -> tuple:
    """Exclude paywalled download rows unless the setting shows them or that status was requested."""
    if status == PaperStatus.PAYWALLED.value or show_paywalled_papers():
        return ()
    return (
        Download.status != PaperStatus.PAYWALLED.value,
        Paper.status != PaperStatus.PAYWALLED.value,
    )


def split_tags(value: str | None) -> list[str]:
    """Split semicolon- or comma-separated keywords / research fields."""
    if not value:
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for part in value.replace(",", ";").split(";"):
        tag = part.strip()
        key = tag.lower()
        if tag and key not in seen:
            seen.add(key)
            tags.append(tag)
    return tags


def _tag_column_matches(column, tag: str):
    text = tag.strip()
    if not text:
        return None
    return or_(
        column == text,
        column.ilike(f"{text};%"),
        column.ilike(f"%; {text}"),
        column.ilike(f"%; {text};%"),
        column.ilike(f"%;{text}"),
        column.ilike(f"%;{text};%"),
    )


def apply_paper_filters(
    stmt,
    *,
    status: str = "",
    downloadable: bool = False,
    open_access: bool = False,
    min_rating: int = 0,
    category: str = "",
    year: int | None = None,
    source: str = "",
    journal: str = "",
    user_id: int | None = None,
):
    if status:
        stmt = stmt.where(Paper.status == status)
    for clause in visible_paper_clauses(status=status):
        stmt = stmt.where(clause)
    if downloadable:
        stmt = stmt.where(downloadable_clause())
    if open_access:
        stmt = stmt.where(open_access_clause())
    if min_rating:
        stmt = stmt.where(Paper.user_rating.is_not(None), Paper.user_rating >= min_rating)
    if category.strip():
        field_match = _tag_column_matches(Paper.research_fields, category)
        keyword_match = _tag_column_matches(Paper.keywords, category)
        stmt = stmt.where(or_(field_match, keyword_match))
    if year:
        stmt = stmt.where(Paper.publication_year == int(year))
    if source.strip():
        stmt = stmt.where(Paper.source == source.strip())
    if journal.strip():
        stmt = stmt.where(Paper.journal == journal.strip())
    if user_id:
        stmt = stmt.where(
            exists().where(
                Download.paper_id == Paper.id,
                Download.downloaded_by_user_id == int(user_id),
            )
        )
    return stmt


def apply_library_text_search(stmt, query: str):
    text = (query or "").strip()
    if not text:
        return stmt
    like = f"%{text}%"
    # Title / DOI / journal / authors first; skip abstract ILIKE (full-table text scan).
    return (
        stmt.outerjoin(PaperAuthor)
        .outerjoin(Author)
        .where(
            or_(
                Paper.title.ilike(like),
                Paper.keywords.ilike(like),
                Paper.research_fields.ilike(like),
                Paper.journal.ilike(like),
                Paper.doi.ilike(like),
                Author.name.ilike(like),
            )
        )
    )


def apply_library_sort(stmt, sort: str, *, latest: bool = False):
    if latest and sort in ("", "relevance"):
        return stmt.order_by(SearchResult.rank, Paper.id.desc())
    if sort == "newest":
        return stmt.order_by(Paper.publication_year.desc(), Paper.id.desc())
    if sort == "oldest":
        return stmt.order_by(Paper.publication_year.asc(), Paper.id.desc())
    if sort == "citations":
        return stmt.order_by(Paper.citation_count.desc(), Paper.id.desc())
    if sort == "rating":
        return stmt.order_by(Paper.user_rating.desc(), Paper.relevance_score.desc(), Paper.id.desc())
    return stmt.order_by(Paper.relevance_score.desc(), Paper.id.desc())


def library_filter_kwargs(
    *,
    status: str = "",
    downloadable: bool = False,
    open_access: bool = False,
    min_rating: int = 0,
    category: str = "",
    year: int | None = None,
    source: str = "",
    journal: str = "",
    user_id: int | None = None,
) -> dict:
    return {
        "status": status,
        "downloadable": downloadable,
        "open_access": open_access,
        "min_rating": min_rating,
        "category": category,
        "year": year,
        "source": source,
        "journal": journal,
        "user_id": user_id,
    }


def query_library(
    session: Session,
    *,
    q: str = "",
    status: str = "",
    downloadable: bool = False,
    open_access: bool = False,
    min_rating: int = 0,
    category: str = "",
    year: int | None = None,
    source: str = "",
    journal: str = "",
    user_id: int | None = None,
    sort: str = "relevance",
    latest_search_id: int | None = None,
    offset: int = 0,
    limit: int = 25,
) -> tuple[list[Paper], int]:
    """Return one page of library papers plus the matching total."""
    filters = library_filter_kwargs(
        status=status,
        downloadable=downloadable,
        open_access=open_access,
        min_rating=min_rating,
        category=category,
        year=year,
        source=source,
        journal=journal,
        user_id=user_id,
    )
    count_stmt = select(func.count(func.distinct(Paper.id)))
    stmt = select(Paper).options(
        selectinload(Paper.downloads).selectinload(Download.downloaded_by),
        # Authors are only needed for Detail/Abstract dialogs — keep one selectinload.
        selectinload(Paper.authors).selectinload(PaperAuthor.author),
    )
    if latest_search_id:
        count_stmt = count_stmt.join(SearchResult, SearchResult.paper_id == Paper.id).where(
            SearchResult.search_query_id == latest_search_id
        )
        stmt = stmt.join(SearchResult, SearchResult.paper_id == Paper.id).where(
            SearchResult.search_query_id == latest_search_id
        )
    count_stmt = apply_library_text_search(count_stmt, q)
    stmt = apply_library_text_search(stmt, q)
    if q.strip():
        stmt = stmt.distinct()
    count_stmt = apply_paper_filters(count_stmt, **filters)
    stmt = apply_paper_filters(stmt, **filters)
    stmt = apply_library_sort(stmt, sort, latest=bool(latest_search_id))
    total = session.scalar(count_stmt) or 0
    papers = list(session.scalars(stmt.offset(offset).limit(limit)).unique().all())
    return papers, total


def library_facets(session: Session, *, force: bool = False, light: bool = False) -> dict:
    """Distinct category / year / source / journal values for library filters.

    light=True skips expensive browse facets (categories/sources/journals) used only
    by unused UI controls — enough for Library KPIs + year filter.
    """
    now = time.monotonic()
    cache_key = "light" if light else "full"
    with _facets_lock:
        cached = _facets_cache.get(cache_key)
        cached_at = _facets_cache_at.get(cache_key, 0.0)
        if not force and cached is not None and (now - cached_at) < _FACETS_TTL_SECONDS:
            return deepcopy(cached)

    visible = visible_paper_clauses()
    years = [
        year
        for year, in session.execute(
            select(Paper.publication_year)
            .where(Paper.publication_year.is_not(None), *visible)
            .distinct()
            .order_by(Paper.publication_year.desc())
        ).all()
    ]
    status_rows = session.execute(
        select(Paper.status, func.count(Paper.id)).where(*visible).group_by(Paper.status)
    ).all()
    status_counts = {code: count for code, count in status_rows if code}
    visible_total = sum(status_counts.values())
    downloadable = session.scalar(select(func.count(Paper.id)).where(downloadable_clause(), *visible)) or 0
    open_access = session.scalar(select(func.count(Paper.id)).where(open_access_clause(), *visible)) or 0
    paywalled = session.scalar(
        select(func.count(Paper.id)).where(Paper.status == PaperStatus.PAYWALLED.value)
    ) or 0

    categories: list[dict] = []
    sources: list[dict] = []
    journals: list[dict] = []
    if not light:
        source_rows = session.execute(
            select(Paper.source, func.count(Paper.id))
            .where(Paper.source.is_not(None), Paper.source != "", *visible)
            .group_by(Paper.source)
            .order_by(func.count(Paper.id).desc())
        ).all()
        journal_rows = session.execute(
            select(Paper.journal, func.count(Paper.id))
            .where(Paper.journal.is_not(None), Paper.journal != "", *visible)
            .group_by(Paper.journal)
            .order_by(func.count(Paper.id).desc())
            .limit(50)
        ).all()
        tag_counts: Counter[str] = Counter()
        # Only scan rows that actually have tags — full-library scans freeze the UI
        # while crawls are writing (100k+ papers).
        tag_stmt = (
            select(Paper.research_fields, Paper.keywords)
            .where(
                *visible,
                or_(
                    and_(Paper.research_fields.is_not(None), Paper.research_fields != ""),
                    and_(Paper.keywords.is_not(None), Paper.keywords != ""),
                ),
            )
            .limit(8_000)
        )
        for fields, keywords in session.execute(tag_stmt).all():
            for tag in split_tags(fields) + split_tags(keywords):
                if len(tag) >= 2:
                    tag_counts[tag] += 1
        ranked = tag_counts.most_common(36)
        peak = ranked[0][1] if ranked else 0
        categories = [
            {
                "name": name,
                "count": count,
                "pct": round((count / peak) * 100, 1) if peak else 0,
            }
            for name, count in ranked
        ]
        sources = [{"slug": slug, "count": count} for slug, count in source_rows]
        journals = [{"name": name, "count": count} for name, count in journal_rows]

    payload = {
        "categories": categories,
        "years": years,
        "sources": sources,
        "journals": journals,
        "status_counts": status_counts,
        "visible_total": visible_total,
        "downloadable": downloadable,
        "downloaded": downloadable,
        "open_access": open_access,
        "paywalled": paywalled,
    }
    with _facets_lock:
        _facets_cache[cache_key] = payload
        _facets_cache_at[cache_key] = time.monotonic()
    return deepcopy(payload)


def dashboard_stats(session: Session, *, force: bool = False) -> dict:
    """Cached dashboard aggregates so the home page stays responsive during crawls."""
    global _dashboard_cache, _dashboard_cache_at
    now = time.monotonic()
    with _facets_lock:
        if (
            not force
            and _dashboard_cache is not None
            and (now - _dashboard_cache_at) < _DASHBOARD_TTL_SECONDS
        ):
            return deepcopy(_dashboard_cache)

    facets = library_facets(session)
    visible = visible_paper_clauses()
    stored_total = session.scalar(select(func.count(Paper.id))) or 0
    oa = session.scalar(select(func.count(Paper.id)).where(Paper.open_access.is_(True), *visible)) or 0
    failed = session.scalar(select(func.count(Download.id)).where(Download.status == "FAILED")) or 0
    searches = session.scalar(select(func.count(SearchQuery.id))) or 0
    no_year = session.scalar(
        select(func.count(Paper.id)).where(Paper.publication_year.is_(None), *visible)
    ) or 0
    years = session.execute(
        select(Paper.publication_year, func.count(Paper.id))
        .where(Paper.publication_year.is_not(None), *visible)
        .group_by(Paper.publication_year)
        .order_by(Paper.publication_year)
    ).all()
    publishers = session.execute(
        select(Paper.publisher, func.count(Paper.id))
        .where(Paper.publisher.is_not(None), Paper.publisher != "", *visible)
        .group_by(Paper.publisher)
        .order_by(func.count(Paper.id).desc())
        .limit(6)
    ).all()
    journals = session.execute(
        select(Paper.journal, func.count(Paper.id))
        .where(Paper.journal.is_not(None), Paper.journal != "", *visible)
        .group_by(Paper.journal)
        .order_by(func.count(Paper.id).desc())
        .limit(6)
    ).all()
    authors = session.execute(
        select(Author.name, func.count(PaperAuthor.id))
        .select_from(PaperAuthor)
        .join(Author, Author.id == PaperAuthor.author_id)
        .join(Paper, Paper.id == PaperAuthor.paper_id)
        .where(*visible)
        .group_by(Author.name)
        .order_by(func.count(PaperAuthor.id).desc())
        .limit(6)
    ).all()
    top_cited_rows = session.execute(
        select(
            Paper.title,
            Paper.doi,
            Paper.publication_year,
            Paper.journal,
            Paper.citation_count,
        )
        .where(Paper.citation_count.is_not(None), *visible)
        .order_by(Paper.citation_count.desc())
        .limit(6)
    ).all()
    recent_rows = session.execute(
        select(SearchQuery.original_query, SearchQuery.status, SearchQuery.created_at)
        .order_by(SearchQuery.created_at.desc())
        .limit(6)
    ).all()
    topics = session.execute(
        select(SearchQuery.original_query, func.count(SearchQuery.id))
        .group_by(SearchQuery.original_query)
        .order_by(func.count(SearchQuery.id).desc())
        .limit(6)
    ).all()

    payload = {
        "stored_total": stored_total,
        "total": int(facets.get("visible_total") or 0),
        "downloadable": int(facets.get("downloadable") or 0),
        "paywalled": int(facets.get("paywalled") or 0),
        "oa": oa,
        "failed": failed,
        "searches": searches,
        "no_year": no_year,
        "status_counts": dict(facets.get("status_counts") or {}),
        "years": [{"year": year, "count": count} for year, count in years],
        "publishers": [{"name": name, "count": count} for name, count in publishers],
        "journals": [{"name": name, "count": count} for name, count in journals],
        "authors": [{"name": name, "count": count} for name, count in authors],
        "top_cited": [
            {
                "title": title,
                "doi": doi,
                "publication_year": year,
                "journal": journal,
                "citation_count": cites,
            }
            for title, doi, year, journal, cites in top_cited_rows
        ],
        "recent": [
            {
                "original_query": query,
                "status": status,
                "created_at": created_at,
            }
            for query, status, created_at in recent_rows
        ],
        "topics": [{"name": name, "count": count} for name, count in topics],
    }
    with _facets_lock:
        _dashboard_cache = payload
        _dashboard_cache_at = time.monotonic()
    return deepcopy(payload)


def set_paper_rating(session: Session, paper_id: int, rating: int) -> Paper | None:
    paper = session.get(Paper, paper_id)
    if paper is None:
        return None
    paper.user_rating = None if rating == 0 else rating
    paper.updated_at = utc_now()
    return paper


def library_search(
    session: Session,
    query: str,
    limit: int = 100,
    *,
    status: str = "",
    downloadable: bool = False,
    min_rating: int = 0,
    category: str = "",
    year: int | None = None,
    source: str = "",
    journal: str = "",
    user_id: int | None = None,
) -> list[Paper]:
    papers, _total = query_library(
        session,
        q=query,
        status=status,
        downloadable=downloadable,
        min_rating=min_rating,
        category=category,
        year=year,
        source=source,
        journal=journal,
        user_id=user_id,
        limit=limit,
    )
    return papers


def _user_filter_label(name: str | None, email: str | None, user_id: int) -> str:
    return (name or "").strip() or (email or "").strip() or f"User {user_id}"


def download_user_options(session: Session, *, include_id: int | None = None) -> list[dict]:
    """Accounts that saved at least one PDF, plus an optional selected user."""
    rows = session.execute(
        select(User.id, User.name, User.email, func.count(Download.id))
        .join(Download, Download.downloaded_by_user_id == User.id)
        .group_by(User.id, User.name, User.email)
        .order_by(func.count(Download.id).desc(), User.name, User.email)
    ).all()
    options = [
        {
            "id": user_id,
            "name": _user_filter_label(name, email, user_id),
            "email": email or "",
            "count": count,
        }
        for user_id, name, email, count in rows
    ]
    selected = int(include_id or 0)
    if selected and selected not in {item["id"] for item in options}:
        row = session.get(User, selected)
        if row is not None:
            options.append(
                {
                    "id": row.id,
                    "name": _user_filter_label(row.name, row.email, row.id),
                    "email": row.email or "",
                    "count": 0,
                }
            )
    return options


def save_fulltext(session: Session, paper_id: int, content: str) -> PaperFulltext:
    row = session.scalar(select(PaperFulltext).where(PaperFulltext.paper_id == paper_id))
    if row is None:
        row = PaperFulltext(paper_id=paper_id, content=content)
        session.add(row)
    else:
        row.content = content
        row.indexed_at = utc_now()
    return row


def fulltext_search(session: Session, query: str, limit: int = 50) -> list[tuple[Paper, str]]:
    like = f"%{query}%"
    stmt = (
        select(Paper, PaperFulltext.content)
        .join(PaperFulltext, PaperFulltext.paper_id == Paper.id)
        .where(PaperFulltext.content.ilike(like))
        .limit(limit)
    )
    return [(paper, snippet[:500]) for paper, snippet in session.execute(stmt).all()]


def enqueue_search_job(session: Session, *, user_id: int | None, query: str, filters: dict) -> SearchJob:
    row = SearchJob(
        user_id=user_id,
        query=query.strip(),
        filters_json=json.dumps(filters),
        status="pending",
    )
    session.add(row)
    session.flush()
    return row


def claim_next_search_job(session: Session, *, max_per_user: int = 1) -> SearchJob | None:
    """Pick the oldest pending job while respecting a per-user running limit."""
    from collections import Counter

    running_counts: Counter[int | None] = Counter(
        session.scalars(select(SearchJob.user_id).where(SearchJob.status == "running")).all()
    )
    pending = session.scalars(
        select(SearchJob)
        .where(SearchJob.status == "pending")
        .order_by(SearchJob.created_at)
    ).all()
    limit = max(1, int(max_per_user))
    for job in pending:
        if running_counts[job.user_id] >= limit:
            continue
        job.status = "running"
        job.started_at = utc_now()
        session.flush()
        return job
    return None


def complete_search_job(
    session: Session,
    job_id: int,
    *,
    status: str = "completed",
    error_message: str | None = None,
    search_query_id: int | None = None,
    papers_found: int | None = None,
    pdfs_downloaded: int | None = None,
    pdfs_failed: int | None = None,
) -> None:
    row = session.get(SearchJob, job_id)
    if row is None:
        return
    row.status = status
    row.error_message = error_message
    row.completed_at = utc_now()
    if search_query_id is not None:
        row.search_query_id = search_query_id
    if papers_found is not None:
        row.papers_found = int(papers_found)
    if pdfs_downloaded is not None:
        row.pdfs_downloaded = int(pdfs_downloaded)
    if pdfs_failed is not None:
        row.pdfs_failed = int(pdfs_failed)


def get_search_job(session: Session, job_id: int) -> SearchJob | None:
    return session.get(SearchJob, job_id)


def list_search_jobs(
    session: Session,
    *,
    user_id: int | None = None,
    statuses: tuple[str, ...] | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    with_user: bool = False,
) -> list[SearchJob]:
    stmt = select(SearchJob).order_by(SearchJob.created_at.desc()).offset(max(0, offset)).limit(limit)
    if user_id is not None:
        stmt = stmt.where(SearchJob.user_id == user_id)
    if statuses:
        stmt = stmt.where(SearchJob.status.in_(statuses))
    needle = (q or "").strip()
    if needle:
        stmt = stmt.where(SearchJob.query.ilike(f"%{needle}%"))
    if with_user:
        stmt = stmt.options(selectinload(SearchJob.user))
    return list(session.scalars(stmt).all())


def count_search_jobs(
    session: Session,
    *,
    user_id: int | None = None,
    statuses: tuple[str, ...] | None = None,
    q: str | None = None,
) -> int:
    stmt = select(func.count(SearchJob.id))
    if user_id is not None:
        stmt = stmt.where(SearchJob.user_id == user_id)
    if statuses:
        stmt = stmt.where(SearchJob.status.in_(statuses))
    needle = (q or "").strip()
    if needle:
        stmt = stmt.where(SearchJob.query.ilike(f"%{needle}%"))
    return int(session.scalar(stmt) or 0)


def queue_position(session: Session, job_id: int) -> int | None:
    """1-based position among pending jobs for the same user."""
    job = session.get(SearchJob, job_id)
    if job is None or job.status != "pending":
        return None
    pending = session.scalars(
        select(SearchJob)
        .where(SearchJob.status == "pending", SearchJob.user_id == job.user_id)
        .order_by(SearchJob.created_at)
    ).all()
    for idx, row in enumerate(pending, start=1):
        if row.id == job_id:
            return idx
    return None


def active_search_job_for_user(session: Session, user_id: int | None) -> SearchJob | None:
    if user_id is None:
        return None
    return session.scalar(
        select(SearchJob)
        .where(SearchJob.user_id == user_id, SearchJob.status == "running")
        .order_by(SearchJob.started_at.desc())
        .limit(1)
    )


def search_jobs_grouped_by_user(session: Session, *, limit: int = 100) -> dict[str, list[SearchJob]]:
    """Return pending/running jobs grouped by username (email)."""
    rows = session.scalars(
        select(SearchJob)
        .where(SearchJob.status.in_(("pending", "running")))
        .options(selectinload(SearchJob.user))
        .order_by(SearchJob.created_at)
        .limit(limit)
    ).all()
    grouped: dict[str, list[SearchJob]] = {}
    for job in rows:
        label = job.user.email if job.user else "Anonymous"
        grouped.setdefault(label, []).append(job)
    return grouped


def active_crawl_job_for_source(session: Session, source: str) -> CrawlJob | None:
    """Return a pending or running crawl for this source, if any."""
    slug = (source or "").strip()
    if not slug:
        return None
    return session.scalar(
        select(CrawlJob)
        .where(CrawlJob.source == slug, CrawlJob.status.in_(("pending", "running")))
        .order_by(CrawlJob.created_at.desc())
        .limit(1)
    )


def enqueue_crawl_job(session: Session, *, user_id: int | None, source: str, filters: dict) -> CrawlJob:
    row = CrawlJob(
        user_id=user_id,
        source=source.strip(),
        filters_json=json.dumps(filters),
        status="pending",
    )
    session.add(row)
    session.flush()
    return row


def claim_next_crawl_job(session: Session, *, max_per_user: int = 1) -> CrawlJob | None:
    from collections import Counter

    running_counts: Counter[int | None] = Counter(
        session.scalars(select(CrawlJob.user_id).where(CrawlJob.status == "running")).all()
    )
    pending = session.scalars(
        select(CrawlJob).where(CrawlJob.status == "pending").order_by(CrawlJob.created_at)
    ).all()
    limit = max(1, int(max_per_user))
    for job in pending:
        if running_counts[job.user_id] >= limit:
            continue
        job.status = "running"
        job.started_at = utc_now()
        session.flush()
        return job
    return None


def complete_crawl_job(
    session: Session,
    job_id: int,
    *,
    status: str = "completed",
    error_message: str | None = None,
    papers_found: int | None = None,
    pdfs_downloaded: int | None = None,
    pdfs_failed: int | None = None,
    records_seen: int | None = None,
    skipped_existing: int | None = None,
) -> None:
    row = session.get(CrawlJob, job_id)
    if row is None:
        return
    row.status = status
    row.error_message = error_message
    row.completed_at = utc_now()
    if papers_found is not None:
        row.papers_found = int(papers_found)
    if pdfs_downloaded is not None:
        row.pdfs_downloaded = int(pdfs_downloaded)
    if pdfs_failed is not None:
        row.pdfs_failed = int(pdfs_failed)
    if records_seen is not None:
        row.records_seen = int(records_seen)
    if skipped_existing is not None:
        row.skipped_existing = int(skipped_existing)


def get_crawl_job(session: Session, job_id: int) -> CrawlJob | None:
    return session.get(CrawlJob, job_id)


def list_crawl_jobs(
    session: Session,
    *,
    user_id: int | None = None,
    statuses: tuple[str, ...] | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    with_user: bool = False,
) -> list[CrawlJob]:
    stmt = select(CrawlJob).order_by(CrawlJob.created_at.desc()).offset(max(0, offset)).limit(limit)
    if user_id is not None:
        stmt = stmt.where(CrawlJob.user_id == user_id)
    if statuses:
        stmt = stmt.where(CrawlJob.status.in_(statuses))
    needle = (q or "").strip()
    if needle:
        stmt = stmt.where(
            or_(CrawlJob.source.ilike(f"%{needle}%"), CrawlJob.filters_json.ilike(f"%{needle}%"))
        )
    if with_user:
        stmt = stmt.options(selectinload(CrawlJob.user))
    return list(session.scalars(stmt).all())


def count_crawl_jobs(
    session: Session,
    *,
    user_id: int | None = None,
    statuses: tuple[str, ...] | None = None,
    q: str | None = None,
) -> int:
    stmt = select(func.count(CrawlJob.id))
    if user_id is not None:
        stmt = stmt.where(CrawlJob.user_id == user_id)
    if statuses:
        stmt = stmt.where(CrawlJob.status.in_(statuses))
    needle = (q or "").strip()
    if needle:
        stmt = stmt.where(
            or_(CrawlJob.source.ilike(f"%{needle}%"), CrawlJob.filters_json.ilike(f"%{needle}%"))
        )
    return int(session.scalar(stmt) or 0)


def crawl_job_keyword(job: CrawlJob) -> str:
    """Keyword/query stored in crawl filters_json, if any."""
    try:
        data = json.loads(job.filters_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("query") or "").strip()


def crawl_queue_position(session: Session, job_id: int) -> int | None:
    job = session.get(CrawlJob, job_id)
    if job is None or job.status != "pending":
        return None
    pending = session.scalars(
        select(CrawlJob)
        .where(CrawlJob.status == "pending", CrawlJob.user_id == job.user_id)
        .order_by(CrawlJob.created_at)
    ).all()
    for idx, row in enumerate(pending, start=1):
        if row.id == job_id:
            return idx
    return None


def active_crawl_job_for_user(session: Session, user_id: int | None) -> CrawlJob | None:
    if user_id is None:
        return None
    return session.scalar(
        select(CrawlJob)
        .where(CrawlJob.user_id == user_id, CrawlJob.status == "running")
        .order_by(CrawlJob.started_at.desc())
        .limit(1)
    )


def active_crawl_job_any(session: Session) -> CrawlJob | None:
    """Any running crawl, else the oldest pending (includes scheduled user_id=None jobs)."""
    running = session.scalar(
        select(CrawlJob)
        .where(CrawlJob.status == "running")
        .order_by(CrawlJob.started_at.desc().nullslast(), CrawlJob.created_at.desc())
        .limit(1)
    )
    if running is not None:
        return running
    return session.scalar(
        select(CrawlJob)
        .where(CrawlJob.status == "pending")
        .order_by(CrawlJob.created_at.asc())
        .limit(1)
    )


def crawl_job_is_scheduled(job: CrawlJob) -> bool:
    if job.user_id is None:
        return True
    try:
        data = json.loads(job.filters_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return bool(isinstance(data, dict) and data.get("scheduled"))


def crawl_jobs_grouped_by_user(session: Session, *, limit: int = 100) -> dict[str, list[CrawlJob]]:
    rows = session.scalars(
        select(CrawlJob)
        .where(CrawlJob.status.in_(("pending", "running")))
        .options(selectinload(CrawlJob.user))
        .order_by(CrawlJob.created_at)
        .limit(limit)
    ).all()
    grouped: dict[str, list[CrawlJob]] = {}
    for job in rows:
        if crawl_job_is_scheduled(job):
            label = "Schedule"
        else:
            label = job.user.email if job.user else "Anonymous"
        grouped.setdefault(label, []).append(job)
    return grouped


def delete_library_paper(session: Session, paper_id: int) -> tuple[str, list[str]]:
    """Remove a paper, related rows, and return title plus local PDF paths to unlink."""
    paper = session.scalar(select(Paper).options(selectinload(Paper.downloads)).where(Paper.id == paper_id))
    if paper is None:
        raise ValueError("Paper not found.")
    title = paper.title
    paths = [row.local_path for row in paper.downloads if row.local_path]
    session.execute(delete(SearchResult).where(SearchResult.paper_id == paper_id))
    session.execute(delete(PaperFulltext).where(PaperFulltext.paper_id == paper_id))
    session.delete(paper)
    session.flush()
    return title, paths
