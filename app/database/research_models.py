"""Research workspace models: projects, screening, extraction, evidence, RAG."""

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models import Base
from app.utils.time import utc_now

REVIEW_TYPES = (
    "literature_review",
    "systematic_review",
    "scoping_review",
    "mapping_study",
    "meta_analysis",
    "general_research",
)
PROJECT_STATUSES = (
    "planning",
    "searching",
    "screening",
    "extraction",
    "synthesis",
    "writing",
    "completed",
    "archived",
)
MEMBER_ROLES = ("owner", "editor", "reviewer", "viewer")
SCREENING_STAGES = ("title_abstract", "full_text")
SCREENING_DECISIONS = ("pending", "include", "exclude", "maybe")
VERIFICATION_STATES = ("ai_suggested", "user_verified", "user_edited", "rejected")


class ResearchProject(Base):
    __tablename__ = "research_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    research_domain: Mapped[str] = mapped_column(String(255), default="")
    review_type: Mapped[str] = mapped_column(String(32), default="literature_review")
    status: Mapped[str] = mapped_column(String(32), default="planning", index=True)
    dual_screening: Mapped[bool] = mapped_column(Boolean, default=False)
    blinded_screening: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_remote_ai: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    members: Mapped[list[ProjectMember]] = relationship(back_populates="project", cascade="all, delete-orphan")
    papers: Mapped[list[ProjectPaper]] = relationship(back_populates="project", cascade="all, delete-orphan")
    questions: Mapped[list[ResearchQuestion]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    project: Mapped[ResearchProject] = relationship(back_populates="members")


class ProjectPaper(Base):
    __tablename__ = "project_papers"
    __table_args__ = (
        UniqueConstraint("project_id", "paper_id", name="uq_project_paper"),
        Index("ix_project_papers_stage", "project_id", "screening_stage", "decision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False, index=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), nullable=False, index=True)
    screening_stage: Mapped[str] = mapped_column(String(32), default="title_abstract")
    decision: Mapped[str] = mapped_column(String(16), default="pending")
    decision_reason: Mapped[str] = mapped_column(String(64), default="")
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    added_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    search_strategy_id: Mapped[int | None] = mapped_column(ForeignKey("search_strategies.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    project: Mapped[ResearchProject] = relationship(back_populates="papers")


class ResearchQuestion(Base):
    __tablename__ = "research_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    project: Mapped[ResearchProject] = relationship(back_populates="questions")


class SearchStrategy(Base):
    __tablename__ = "search_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    expanded_query: Mapped[str] = mapped_column(Text, default="")
    synonyms: Mapped[str] = mapped_column(Text, default="")
    boolean_string: Mapped[str] = mapped_column(Text, default="")
    provider_config_json: Mapped[str] = mapped_column(Text, default="{}")
    year_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    year_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str] = mapped_column(String(64), default="")
    document_type: Mapped[str] = mapped_column(String(64), default="")
    open_access_only: Mapped[bool] = mapped_column(Boolean, default=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_result_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class SearchRun(Base):
    """Immutable record of a strategy execution for reproducibility."""

    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False, index=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("search_strategies.id"), nullable=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), default="")
    filters_json: Mapped[str] = mapped_column(Text, default="{}")
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class EligibilityCriterion(Base):
    __tablename__ = "eligibility_criteria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class ExclusionReason(Base):
    __tablename__ = "exclusion_reasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ScreeningDecision(Base):
    __tablename__ = "screening_decisions"
    __table_args__ = (
        UniqueConstraint("project_paper_id", "reviewer_id", "stage", name="uq_screening_decision"),
        Index("ix_screening_decisions_paper", "project_paper_id", "stage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_paper_id: Mapped[int] = mapped_column(ForeignKey("project_papers.id"), nullable=False)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class QualityChecklist(Base):
    __tablename__ = "quality_checklists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), default="Quality assessment")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    questions: Mapped[list[QualityQuestion]] = relationship(back_populates="checklist", cascade="all, delete-orphan")


class QualityQuestion(Base):
    __tablename__ = "quality_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checklist_id: Mapped[int] = mapped_column(ForeignKey("quality_checklists.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    answer_type: Mapped[str] = mapped_column(String(16), default="yes_partial_no")
    position: Mapped[int] = mapped_column(Integer, default=0)

    checklist: Mapped[QualityChecklist] = relationship(back_populates="questions")


class QualityAssessment(Base):
    __tablename__ = "quality_assessments"
    __table_args__ = (UniqueConstraint("project_paper_id", "reviewer_id", name="uq_quality_assessment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_paper_id: Mapped[int] = mapped_column(ForeignKey("project_papers.id"), nullable=False, index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class QualityAnswer(Base):
    __tablename__ = "quality_answers"
    __table_args__ = (UniqueConstraint("assessment_id", "question_id", name="uq_quality_answer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("quality_assessments.id"), nullable=False, index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("quality_questions.id"), nullable=False)
    value: Mapped[str] = mapped_column(Text, default="")
    numeric_score: Mapped[float | None] = mapped_column(Float, nullable=True)


class ExtractionSchema(Base):
    __tablename__ = "extraction_schemas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), default="Default academic extraction")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    fields: Mapped[list[ExtractionField]] = relationship(back_populates="schema", cascade="all, delete-orphan")


class ExtractionField(Base):
    __tablename__ = "extraction_fields"
    __table_args__ = (UniqueConstraint("schema_id", "key", name="uq_extraction_field_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schema_id: Mapped[int] = mapped_column(ForeignKey("extraction_schemas.id"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    field_type: Mapped[str] = mapped_column(String(16), default="text")
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)

    schema: Mapped[ExtractionSchema] = relationship(back_populates="fields")


class ExtractedValue(Base):
    __tablename__ = "extracted_values"
    __table_args__ = (UniqueConstraint("project_paper_id", "field_id", name="uq_extracted_value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_paper_id: Mapped[int] = mapped_column(ForeignKey("project_papers.id"), nullable=False, index=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("extraction_fields.id"), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    verification_state: Mapped[str] = mapped_column(String(16), default="user_edited")
    extraction_method: Mapped[str] = mapped_column(String(16), default="manual")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    modified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class EvidenceSource(Base):
    __tablename__ = "evidence_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    extracted_value_id: Mapped[int | None] = mapped_column(ForeignKey("extracted_values.id"), nullable=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False, index=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), nullable=False, index=True)
    research_question_id: Mapped[int | None] = mapped_column(ForeignKey("research_questions.id"), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str] = mapped_column(String(128), default="")
    evidence_text: Mapped[str] = mapped_column(Text, default="")
    stance: Mapped[str] = mapped_column(String(16), default="supporting")
    extraction_method: Mapped[str] = mapped_column(String(16), default="manual")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verified_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class PaperAnnotation(Base):
    __tablename__ = "paper_annotations"
    __table_args__ = (Index("ix_annotations_user_paper", "user_id", "paper_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), nullable=False)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("research_projects.id"), nullable=True)
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    annotation_type: Mapped[str] = mapped_column(String(16), default="highlight")
    selected_text: Mapped[str] = mapped_column(Text, default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    color_key: Mapped[str] = mapped_column(String(16), default="yellow")
    position_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (Index("ix_chunks_paper_page", "paper_id", "page_number", "chunk_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), nullable=False, index=True)
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    section: Mapped[str] = mapped_column(String(128), default="")
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    embedding_model: Mapped[str] = mapped_column(String(64), default="")
    embedding_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Theme(Base):
    __tablename__ = "themes"
    __table_args__ = (UniqueConstraint("project_id", "slug", name="uq_theme_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    suggested: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class EvidenceTheme(Base):
    __tablename__ = "evidence_themes"
    __table_args__ = (UniqueConstraint("theme_id", "evidence_id", name="uq_evidence_theme"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"), nullable=False, index=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("evidence_sources.id"), nullable=False)


class ProjectActivity(Base):
    __tablename__ = "project_activities"
    __table_args__ = (Index("ix_project_activity_created", "project_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class DatasetRecord(Base):
    __tablename__ = "dataset_records"
    __table_args__ = (UniqueConstraint("project_id", "normalized_name", name="uq_dataset_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(64), default="")
    domain: Mapped[str] = mapped_column(String(128), default="")
    record_count: Mapped[str] = mapped_column(String(64), default="")
    classes: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    license: Mapped[str] = mapped_column(String(128), default="")
    public: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    paper_count: Mapped[int] = mapped_column(Integer, default=0)


class AlgorithmRecord(Base):
    __tablename__ = "algorithm_records"
    __table_args__ = (UniqueConstraint("project_id", "normalized_name", name="uq_algorithm_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    paper_count: Mapped[int] = mapped_column(Integer, default=0)
    datasets: Mapped[str] = mapped_column(Text, default="")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")


class ReproducibilityRecord(Base):
    __tablename__ = "reproducibility_records"
    __table_args__ = (UniqueConstraint("project_paper_id", name="uq_repro_paper"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_paper_id: Mapped[int] = mapped_column(ForeignKey("project_papers.id"), nullable=False)
    code_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    code_url: Mapped[str] = mapped_column(Text, default="")
    dataset_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    dataset_url: Mapped[str] = mapped_column(Text, default="")
    environment_described: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    hyperparameters_reported: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    random_seed_reported: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pretrained_model_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    license_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reproduced_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    reproduction_notes: Mapped[str] = mapped_column(Text, default="")


class CfpBookmark(Base):
    __tablename__ = "cfp_bookmarks"
    __table_args__ = (UniqueConstraint("user_id", "cfp_id", name="uq_cfp_bookmark"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    cfp_id: Mapped[int] = mapped_column(ForeignKey("cfp_calls.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class ResearchJob(Base):
    __tablename__ = "research_jobs"
    __table_args__ = (Index("ix_research_jobs_status", "status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("research_projects.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
