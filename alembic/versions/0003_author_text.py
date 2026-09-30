"""Widen author name columns. SQLite did not enforce VARCHAR(512).

Revision ID: 0003_author_text
Revises: 0002_ebooks
Create Date: 2026-09-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_author_text"
down_revision = "0002_ebooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    inspector = sa.inspect(bind)
    if "authors" not in inspector.get_table_names():
        return
    columns = {col["name"]: str(col["type"]).lower() for col in inspector.get_columns("authors")}
    indexes = list(inspector.get_indexes("authors"))
    if "varchar" in columns.get("name", "") or "character" in columns.get("name", ""):
        op.execute(sa.text("ALTER TABLE authors MODIFY name TEXT NOT NULL"))
    if "varchar" in columns.get("normalized_name", "") or "character" in columns.get("normalized_name", ""):
        for idx in indexes:
            if (idx.get("column_names") or []) == ["normalized_name"] and idx.get("name"):
                op.execute(sa.text(f"ALTER TABLE authors DROP INDEX `{idx['name']}`"))
        op.execute(sa.text("ALTER TABLE authors MODIFY normalized_name TEXT"))
        op.execute(sa.text("CREATE INDEX ix_authors_normalized_name ON authors (normalized_name(255))"))
    orcid = columns.get("orcid", "")
    if "varchar(64)" in orcid.replace(" ", ""):
        op.execute(sa.text("ALTER TABLE authors MODIFY orcid VARCHAR(255) NULL"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    op.execute(sa.text("ALTER TABLE authors DROP INDEX ix_authors_normalized_name"))
    op.execute(sa.text("ALTER TABLE authors MODIFY name VARCHAR(512) NOT NULL"))
    op.execute(sa.text("ALTER TABLE authors MODIFY normalized_name VARCHAR(512)"))
    op.execute(sa.text("CREATE INDEX ix_authors_normalized_name ON authors (normalized_name)"))
    op.execute(sa.text("ALTER TABLE authors MODIFY orcid VARCHAR(64) NULL"))
