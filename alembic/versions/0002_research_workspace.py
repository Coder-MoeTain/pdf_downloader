"""Research workspace tables.

Revision ID: 0002_research_workspace
Revises: 0001_research_platform
Create Date: 2026-09-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.database.models import Base
import app.database.research_models  # noqa: F401

revision = "0002_research_workspace"
down_revision = "0001_research_platform"
branch_labels = None
depends_on = None

RESEARCH_TABLES = {
    "research_projects",
    "project_members",
    "project_papers",
    "research_questions",
    "search_strategies",
    "search_runs",
    "eligibility_criteria",
    "exclusion_reasons",
    "screening_decisions",
    "quality_checklists",
    "quality_questions",
    "quality_assessments",
    "quality_answers",
    "extraction_schemas",
    "extraction_fields",
    "extracted_values",
    "evidence_sources",
    "paper_annotations",
    "document_chunks",
    "themes",
    "evidence_themes",
    "project_activities",
    "dataset_records",
    "algorithm_records",
    "reproducibility_records",
    "cfp_bookmarks",
    "research_jobs",
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name in RESEARCH_TABLES and table.name not in existing:
            table.create(bind)
    if "saved_searches" in existing:
        columns = {col["name"] for col in inspector.get_columns("saved_searches")}
        with op.batch_alter_table("saved_searches") as batch:
            if "alert_frequency" not in columns:
                batch.add_column(sa.Column("alert_frequency", sa.String(length=16), server_default="weekly"))
            if "last_result_count" not in columns:
                batch.add_column(sa.Column("last_result_count", sa.Integer(), server_default="0"))
            if "new_paper_count" not in columns:
                batch.add_column(sa.Column("new_paper_count", sa.Integer(), server_default="0"))
            if "project_id" not in columns:
                batch.add_column(sa.Column("project_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in RESEARCH_TABLES and table.name in existing:
            table.drop(bind)
