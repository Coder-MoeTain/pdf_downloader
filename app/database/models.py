"""SQLAlchemy ORM models for SQLite persistence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.utils.time import utc_now


class Base(DeclarativeBase):
    pass


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(512), index=True)
    affiliations: Mapped[str | None] = mapped_column(Text, nullable=True)
    orcid: Mapped[str | None] = mapped_column(String(64), nullable=True)

    papers: Mapped[list["PaperAuthor"]] = relationship(back_populates="author")


class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = (
        Index("ix_papers_doi", "doi"),
        Index("ix_papers_pmid", "pmid"),
        Index("ix_papers_arxiv", "arxiv_id"),
        Index("ix_papers_norm_title", "normalized_title"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_title: Mapped[str] = mapped_column(Text, default="")
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pmid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pmcid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    openalex_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    semantic_scholar_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publication_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    journal: Mapped[str | None] = mapped_column(String(512), nullable=True)
    conference: Mapped[str | None] = mapped_column(String(512), nullable=True)
    volume: Mapped[str | None] = mapped_column(String(64), nullable=True)
    issue: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pages: Mapped[str | None] = mapped_column(String(64), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    research_fields: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    open_access: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    license: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_sources: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    user_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="FOUND")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    authors: Mapped[list["PaperAuthor"]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    downloads: Mapped[list["Download"]] = relationship(back_populates="paper", cascade="all, delete-orphan")


class PaperAuthor(Base):
    __tablename__ = "paper_authors"
    __table_args__ = (UniqueConstraint("paper_id", "author_id", "position", name="uq_paper_author_pos"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), nullable=False)
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0)

    paper: Mapped[Paper] = relationship(back_populates="authors")
    author: Mapped[Author] = relationship(back_populates="papers")


class SearchQuery(Base):
    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    original_query: Mapped[str] = mapped_column(Text, nullable=False)
    expanded_queries: Mapped[str | None] = mapped_column(Text, nullable=True)
    filters_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    results: Mapped[list["SearchResult"]] = relationship(back_populates="search", cascade="all, delete-orphan")


class SearchResult(Base):
    __tablename__ = "search_results"
    __table_args__ = (UniqueConstraint("search_query_id", "paper_id", name="uq_search_paper"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_query_id: Mapped[int] = mapped_column(ForeignKey("search_queries.id"), nullable=False)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)

    search: Mapped[SearchQuery] = relationship(back_populates="results")
    paper: Mapped[Paper] = relationship()


class Download(Base):
    __tablename__ = "downloads"
    __table_args__ = (Index("ix_downloads_sha256", "sha256"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), nullable=False)
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="FOUND")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    paper: Mapped[Paper] = relationship(back_populates="downloads")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    google_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    picture: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class PaperFulltext(Base):
    __tablename__ = "paper_fulltext"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), unique=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    indexed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
