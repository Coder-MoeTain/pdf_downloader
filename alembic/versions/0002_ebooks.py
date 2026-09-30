"""Ebook metadata columns on papers.

Revision ID: 0002_ebooks
Revises: 0001_research_platform
Create Date: 2026-09-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_ebooks"
down_revision = "0001_research_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("papers")}
    with op.batch_alter_table("papers") as batch:
        if "work_type" not in columns:
            batch.add_column(sa.Column("work_type", sa.String(length=16), server_default="article"))
        if "cover_url" not in columns:
            batch.add_column(sa.Column("cover_url", sa.Text(), nullable=True))
        if "cover_path" not in columns:
            batch.add_column(sa.Column("cover_path", sa.Text(), nullable=True))
        if "category" not in columns:
            batch.add_column(sa.Column("category", sa.String(length=128), nullable=True))
    indexes = {idx["name"] for idx in inspector.get_indexes("papers")}
    if "ix_papers_work_type" not in indexes:
        op.create_index("ix_papers_work_type", "papers", ["work_type"])
    if "ix_papers_category" not in indexes:
        op.create_index("ix_papers_category", "papers", ["category"])


def downgrade() -> None:
    op.drop_index("ix_papers_category", table_name="papers")
    op.drop_index("ix_papers_work_type", table_name="papers")
    with op.batch_alter_table("papers") as batch:
        batch.drop_column("category")
        batch.drop_column("cover_path")
        batch.drop_column("cover_url")
        batch.drop_column("work_type")
