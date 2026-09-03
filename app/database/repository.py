"""Repository helpers for papers, searches, and downloads."""

from __future__ import annotations

import json

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.database.models import Author, Download, Paper, PaperAuthor, PaperFulltext, Provider, SearchQuery, SearchResult
from app.models.paper import AuthorRecord, PaperRecord, PaperStatus
from app.utils.filename import normalize_title
from app.utils.time import utc_now


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


def create_search_query(session: Session, original: str, expanded: list[str], filters: dict) -> SearchQuery:
    row = SearchQuery(
        original_query=original,
        expanded_queries=json.dumps(expanded),
        filters_json=json.dumps(filters),
        status="running",
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
    session.flush()
    return row


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
    """Papers with a legally available PDF URL or an already-downloaded file."""
    return or_(
        Paper.status == PaperStatus.DOWNLOADED.value,
        and_(
            Paper.pdf_url.is_not(None),
            Paper.pdf_url != "",
            Paper.status.notin_(
                [
                    PaperStatus.PAYWALLED.value,
                    PaperStatus.SKIPPED.value,
                    PaperStatus.NO_PDF.value,
                ]
            ),
        ),
    )


def show_paywalled_papers() -> bool:
    """True when paywalled records should appear in library lists and analytics."""
    try:
        from app.config import get_runtime_config

        return bool(get_runtime_config().show_paywalled)
    except Exception:
        return True


def visible_paper_clauses(*, status: str = "") -> tuple:
    """Exclude paywalled papers unless the setting shows them or the user asked for that status."""
    if status == PaperStatus.PAYWALLED.value or show_paywalled_papers():
        return ()
    return (Paper.status != PaperStatus.PAYWALLED.value,)


def visible_download_clauses(*, status: str = "") -> tuple:
    """Exclude paywalled download rows unless the setting shows them or that status was requested."""
    if status == PaperStatus.PAYWALLED.value or show_paywalled_papers():
        return ()
    return (
        Download.status != PaperStatus.PAYWALLED.value,
        Paper.status != PaperStatus.PAYWALLED.value,
    )


def apply_paper_filters(stmt, *, status: str = "", downloadable: bool = False, min_rating: int = 0):
    if status:
        stmt = stmt.where(Paper.status == status)
    else:
        for clause in visible_paper_clauses():
            stmt = stmt.where(clause)
    if downloadable:
        stmt = stmt.where(downloadable_clause())
    if min_rating:
        stmt = stmt.where(Paper.user_rating.is_not(None), Paper.user_rating >= min_rating)
    return stmt


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
) -> list[Paper]:
    like = f"%{query}%"
    stmt = (
        select(Paper)
        .outerjoin(PaperAuthor)
        .outerjoin(Author)
        .where(
            or_(
                Paper.title.ilike(like),
                Paper.abstract.ilike(like),
                Paper.keywords.ilike(like),
                Paper.journal.ilike(like),
                Paper.doi.ilike(like),
                Author.name.ilike(like),
            )
        )
        .options(selectinload(Paper.downloads))
        .distinct()
        .order_by(Paper.relevance_score.desc())
    )
    stmt = apply_paper_filters(stmt, status=status, downloadable=downloadable, min_rating=min_rating)
    stmt = stmt.limit(limit)
    return list(session.scalars(stmt).unique().all())


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
