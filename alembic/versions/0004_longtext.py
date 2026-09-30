"""MySQL TEXT is 64KB; SQLite abstracts and extracted PDFs can be larger.

Revision ID: 0004_longtext
Revises: 0003_author_text
Create Date: 2026-09-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_longtext"
down_revision = "0003_author_text"
branch_labels = None
depends_on = None

_PAPER_LONGTEXT = (
    ("title", " NOT NULL"),
    ("normalized_title", ""),
    ("abstract", ""),
    ("keywords", ""),
    ("research_fields", ""),
    ("url", ""),
    ("pdf_url", ""),
    ("metadata_sources", ""),
    ("cover_url", ""),
    ("cover_path", ""),
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "authors" in tables:
        op.execute(sa.text("ALTER TABLE authors MODIFY name LONGTEXT NOT NULL"))
        op.execute(sa.text("ALTER TABLE authors MODIFY normalized_name LONGTEXT"))
        op.execute(sa.text("ALTER TABLE authors MODIFY affiliations LONGTEXT"))
    if "papers" in tables:
        for idx in inspector.get_indexes("papers"):
            if (idx.get("column_names") or []) == ["normalized_title"] and idx.get("name"):
                op.execute(sa.text(f"ALTER TABLE papers DROP INDEX `{idx['name']}`"))
        for column, extra in _PAPER_LONGTEXT:
            op.execute(sa.text(f"ALTER TABLE papers MODIFY `{column}` LONGTEXT{extra}"))
        op.execute(sa.text("CREATE INDEX ix_papers_norm_title ON papers (normalized_title(255))"))
    if "paper_fulltext" in tables:
        op.execute(sa.text("ALTER TABLE paper_fulltext MODIFY content LONGTEXT"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    op.execute(sa.text("ALTER TABLE papers DROP INDEX ix_papers_norm_title"))
    op.execute(sa.text("ALTER TABLE papers MODIFY title TEXT NOT NULL"))
    op.execute(sa.text("ALTER TABLE papers MODIFY abstract TEXT"))
    op.execute(sa.text("CREATE INDEX ix_papers_norm_title ON papers (normalized_title(255))"))
