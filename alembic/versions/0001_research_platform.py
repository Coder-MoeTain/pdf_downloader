"""Research platform identity, user library, and CFP verification columns.

Revision ID: 0001_research_platform
Revises:
Create Date: 2026-09-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_research_platform"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "paper_identifiers" not in tables:
        op.create_table(
            "paper_identifiers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id"), nullable=False),
            sa.Column("scheme", sa.String(length=32), nullable=False),
            sa.Column("value", sa.String(length=512), nullable=False),
            sa.Column("normalized_value", sa.String(length=512), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("scheme", "normalized_value", name="uq_paper_identifier"),
        )
    if "user_papers" not in tables:
        op.create_table(
            "user_papers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id"), nullable=False),
            sa.Column("rating", sa.Integer(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("tags", sa.Text(), nullable=True),
            sa.Column("reading_status", sa.String(length=16), server_default="unread"),
            sa.Column("bookmarked", sa.Boolean(), server_default=sa.false()),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("user_id", "paper_id", name="uq_user_paper"),
        )
    if "collections" not in tables:
        op.create_table(
            "collections",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("slug", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    if "collection_papers" not in tables:
        op.create_table(
            "collection_papers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("collection_id", sa.Integer(), sa.ForeignKey("collections.id"), nullable=False),
            sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id"), nullable=False),
            sa.Column("added_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("collection_id", "paper_id", name="uq_collection_paper"),
        )
    if "saved_searches" not in tables:
        op.create_table(
            "saved_searches",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("query", sa.Text(), nullable=False),
            sa.Column("filters_json", sa.Text(), server_default="{}"),
            sa.Column("alert_enabled", sa.Boolean(), server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
        )
    if "cfp_calls" in tables:
        columns = {col["name"] for col in inspector.get_columns("cfp_calls")}
        with op.batch_alter_table("cfp_calls") as batch:
            if "deadline_source" not in columns:
                batch.add_column(sa.Column("deadline_source", sa.String(length=64), server_default="wikicfp"))
            if "deadline_verified" not in columns:
                batch.add_column(sa.Column("deadline_verified", sa.Boolean(), server_default=sa.false()))
            if "deadline_confidence" not in columns:
                batch.add_column(sa.Column("deadline_confidence", sa.String(length=16), server_default="estimated"))
            if "official_website" not in columns:
                batch.add_column(sa.Column("official_website", sa.Text(), nullable=True))
            if "source_url" not in columns:
                batch.add_column(sa.Column("source_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_table("saved_searches")
    op.drop_table("collection_papers")
    op.drop_table("collections")
    op.drop_table("user_papers")
    op.drop_table("paper_identifiers")
