"""Copy the SQLite paper library and settings files into configured MySQL."""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine

from app.config import ROOT_DIR, load_config
from app.database.models import Base
from app.database.settings_models import SettingsBase
from app.utils.logger import get_logger

logger = get_logger("app.migrate")

LIBRARY_TABLES = (
    "providers",
    "authors",
    "users",
    "papers",
    "paper_identifiers",
    "paper_authors",
    "search_queries",
    "search_jobs",
    "crawl_jobs",
    "search_results",
    "downloads",
    "lms_exports",
    "usage_events",
    "audit_logs",
    "paper_fulltext",
    "cfp_calls",
    "github_repos",
    "user_papers",
    "collections",
    "collection_papers",
    "saved_searches",
)

SETTINGS_TABLES = ("app_settings", "academic_sources")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VARCHAR_RE = re.compile(r"(?:var)?char\s*\(\s*(\d+)\s*\)", re.I)
_CHUNK = 400
_CHUNK_BY_TABLE = {"papers": 50, "paper_fulltext": 5, "search_results": 200}
_MYSQL_INDEXED_VARCHAR_MAX = 768


@dataclass
class MigrateReport:
    tables: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    dry_run: bool = False

    def add(self, table: str, count: int) -> None:
        self.tables[table] = count


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _quote(name: str, dialect: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name}")
    return f"`{name}`" if dialect == "mysql" else f'"{name}"'


def _open_sqlite(path: Path) -> Engine:
    if not path.is_file():
        raise FileNotFoundError(path)
    engine = create_engine(
        sqlite_url(path),
        echo=False,
        future=True,
        connect_args={"check_same_thread": False, "timeout": 60},
    )
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        try:
            conn.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))
        except Exception:
            pass
        conn.commit()
    return engine


def _table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _columns(engine: Engine, table: str) -> list[str]:
    return [col["name"] for col in inspect(engine).get_columns(table)]


def _column_types(engine: Engine, table: str) -> dict[str, str]:
    return {col["name"]: str(col["type"]) for col in inspect(engine).get_columns(table)}


def _common_columns(source: Engine, dest: Engine, table: str) -> list[str]:
    src = [name for name in _columns(source, table) if _IDENT_RE.match(name)]
    dest_names = set(_columns(dest, table))
    return [name for name in src if name in dest_names]


def _declared_length(type_name: str) -> int | None:
    match = _VARCHAR_RE.search(type_name or "")
    return int(match.group(1)) if match else None


def _mysql_string_kind(type_name: str) -> str | None:
    compact = re.sub(r"\s+", "", (type_name or "").lower())
    if "longtext" in compact or "longblob" in compact:
        return "longtext"
    if "mediumtext" in compact or "mediumblob" in compact:
        return "mediumtext"
    if "tinytext" in compact or "tinyblob" in compact:
        return "tinytext"
    if compact.startswith("text") or compact.startswith("clob"):
        return "text"
    if _VARCHAR_RE.search(type_name or ""):
        return "varchar"
    return None


def _sqlite_max_chars(conn: Connection, table: str, column: str, dialect: str) -> int:
    value = conn.execute(
        text(f"SELECT MAX(LENGTH({_quote(column, dialect)})) FROM {_quote(table, dialect)}")
    ).scalar()
    return int(value or 0)


def _widen_dest_string_columns(source: Engine, dest: Engine, table: str) -> None:
    """Grow MySQL VARCHAR/TEXT columns. MySQL TEXT is 64KB; SQLite TEXT is unbounded."""
    if dest.dialect.name != "mysql":
        return
    insp = inspect(dest)
    try:
        insp.clear_cache()
    except Exception:
        pass
    dest_cols = {col["name"]: col for col in insp.get_columns(table)}
    indexes = list(insp.get_indexes(table))
    src_cols = set(_columns(source, table))
    src_dialect = source.dialect.name
    with source.connect() as src_conn:
        needed: list[tuple[str, int, object, list, str]] = []
        for name, col in dest_cols.items():
            if name not in src_cols:
                continue
            kind = _mysql_string_kind(str(col["type"]))
            if kind is None or kind == "longtext":
                continue
            max_len = _sqlite_max_chars(src_conn, table, name, src_dialect)
            varchar_limit = _declared_length(str(col["type"]))
            overflow = False
            if kind in {"tinytext", "text", "mediumtext"}:
                overflow = True
            elif kind == "varchar" and varchar_limit is not None and max_len > varchar_limit:
                overflow = True
            if not overflow:
                continue
            covering = [idx for idx in indexes if (idx.get("column_names") or []) == [name]]
            needed.append((name, max_len, col, covering, kind))
    if not needed:
        return
    with dest.begin() as dest_conn:
        _disable_fks(dest_conn, "mysql")
        table_sql = _quote(table, "mysql")
        for name, max_len, col, covering, kind in needed:
            col_sql = _quote(name, "mysql")
            unique = any(bool(idx.get("unique")) for idx in covering)
            logger.warning(
                "Widening %s.%s from %s (SQLite max length %s)",
                table,
                name,
                col["type"],
                max_len,
            )
            for idx in covering:
                idx_name = idx.get("name")
                if not idx_name:
                    continue
                dest_conn.execute(text(f"ALTER TABLE {table_sql} DROP INDEX {_quote(idx_name, 'mysql')}"))
            null_sql = "" if col.get("nullable", True) else " NOT NULL"
            if unique and kind == "varchar":
                new_len = min(max(max_len, 1), _MYSQL_INDEXED_VARCHAR_MAX)
                dest_conn.execute(text(f"ALTER TABLE {table_sql} MODIFY {col_sql} VARCHAR({new_len}){null_sql}"))
                for idx in covering:
                    idx_name = idx.get("name")
                    if idx_name:
                        dest_conn.execute(
                            text(f"CREATE UNIQUE INDEX {_quote(idx_name, 'mysql')} ON {table_sql} ({col_sql})")
                        )
            else:
                dest_conn.execute(text(f"ALTER TABLE {table_sql} MODIFY {col_sql} LONGTEXT{null_sql}"))
                for idx in covering:
                    idx_name = idx.get("name")
                    if not idx_name:
                        continue
                    dest_conn.execute(
                        text(
                            f"CREATE INDEX {_quote(idx_name, 'mysql')} ON {table_sql} ({col_sql}(255))"
                        )
                    )
    try:
        inspect(dest).clear_cache()
    except Exception:
        pass


def _coerce(value: object, dest_type: str) -> object:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value).decode("utf-8", errors="replace")
    type_name = dest_type.lower()
    if value == "" and any(
        token in type_name for token in ("int", "float", "double", "numeric", "decimal", "date", "time")
    ):
        return None
    if isinstance(value, bool):
        return int(value)
    if "bool" in type_name or "tinyint(1)" in type_name:
        text_value = str(value).strip().lower()
        if text_value in {"1", "true", "yes"}:
            return 1
        if text_value in {"0", "false", "no"}:
            return 0
    limit = _declared_length(dest_type)
    if limit is not None and isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


def _disable_fks(conn: Connection, dialect: str) -> None:
    if dialect == "mysql":
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    else:
        conn.execute(text("PRAGMA foreign_keys=OFF"))


def _truncate(conn: Connection, table: str, dialect: str) -> None:
    quoted = _quote(table, dialect)
    if dialect == "mysql":
        conn.execute(text(f"TRUNCATE TABLE {quoted}"))
    else:
        conn.execute(text(f"DELETE FROM {quoted}"))


def _reset_autoincrement(conn: Connection, table: str, dialect: str) -> None:
    if dialect != "mysql":
        return
    quoted = _quote(table, dialect)
    maximum = conn.execute(text(f"SELECT MAX(id) FROM {quoted}")).scalar()
    if maximum is None:
        return
    conn.execute(text(f"ALTER TABLE {quoted} AUTO_INCREMENT = {int(maximum) + 1}"))


def _truncate_tables(dest: Engine, tables: tuple[str, ...]) -> None:
    dest_tables = _table_names(dest)
    dialect = dest.dialect.name
    with dest.begin() as conn:
        _disable_fks(conn, dialect)
        for table in reversed(tables):
            if table in dest_tables:
                _truncate(conn, table, dialect)


def _count(conn: Connection, table: str, dialect: str) -> int:
    value = conn.execute(text(f"SELECT COUNT(*) FROM {_quote(table, dialect)}")).scalar()
    return int(value or 0)


def _copy_table(source: Engine, dest: Engine, table: str, *, dry_run: bool, report: MigrateReport) -> None:
    dest_tables = _table_names(dest)
    src_tables = _table_names(source)
    if table not in src_tables:
        report.skipped.append(f"{table} (missing in SQLite)")
        return
    if table not in dest_tables:
        report.skipped.append(f"{table} (missing in destination)")
        return
    columns = _common_columns(source, dest, table)
    if not columns:
        report.skipped.append(f"{table} (no shared columns)")
        return
    dest_dialect = dest.dialect.name
    src_dialect = source.dialect.name
    if not dry_run:
        _widen_dest_string_columns(source, dest, table)
    types = _column_types(dest, table)
    with source.connect() as src_conn:
        count = _count(src_conn, table, src_dialect)
        report.add(table, count)
        if dry_run or count == 0:
            return
        col_sql = ", ".join(_quote(name, dest_dialect) for name in columns)
        placeholders = ", ".join(f":{name}" for name in columns)
        insert_sql = text(f"INSERT INTO {_quote(table, dest_dialect)} ({col_sql}) VALUES ({placeholders})")
        chunk_size = _CHUNK_BY_TABLE.get(table, _CHUNK)
        result = src_conn.execution_options(stream_results=True).execute(
            text(f"SELECT * FROM {_quote(table, src_dialect)}")
        )
        with dest.begin() as dest_conn:
            _disable_fks(dest_conn, dest_dialect)
            while True:
                fetched = result.fetchmany(chunk_size)
                if not fetched:
                    break
                batch = [
                    {name: _coerce(row._mapping.get(name), types.get(name, "")) for name in columns} for row in fetched
                ]
                dest_conn.execute(insert_sql, batch)
            if "id" in columns:
                try:
                    _reset_autoincrement(dest_conn, table, dest_dialect)
                except Exception as exc:
                    logger.warning("Could not reset AUTO_INCREMENT on %s: %s", table, exc)
    logger.info("Copied %s: %s row(s)", table, count)


def _occupied_library_tables(dest: Engine) -> list[str]:
    occupied: list[str] = []
    dest_tables = _table_names(dest)
    with dest.connect() as conn:
        for table in ("users", "papers"):
            if table in dest_tables and _count(conn, table, dest.dialect.name) > 0:
                occupied.append(table)
    return occupied


def run_migration(
    sqlite_path: Path,
    settings_path: Path | None,
    *,
    dest_engine: Engine | None = None,
    replace: bool = False,
    dry_run: bool = False,
    library: bool = True,
    settings: bool = True,
    progress: Callable[[str], None] | None = None,
) -> MigrateReport:
    """Copy SQLite rows into the destination engine (MySQL in production)."""
    cfg = load_config()
    dest = dest_engine
    if dest is None:
        if not cfg.uses_mysql:
            raise RuntimeError("MYSQL_HOST is empty. Set MYSQL_* in .env before migrating.")
        from app.database.connection import get_engine, init_db

        init_db()
        dest = get_engine()
    Base.metadata.create_all(dest)
    SettingsBase.metadata.create_all(dest)

    source: Engine | None = None
    report = MigrateReport(dry_run=dry_run)
    say = progress or (lambda message: logger.info(message))

    try:
        if library:
            source = _open_sqlite(sqlite_path)
            if not dry_run and not replace:
                occupied = _occupied_library_tables(dest)
                if occupied:
                    raise RuntimeError(
                        "MySQL already has rows in "
                        + ", ".join(occupied)
                        + ". Re-run with --replace to wipe those tables and copy from SQLite."
                    )
            say(f"Library SQLite: {sqlite_path}")
            if replace and not dry_run:
                _truncate_tables(dest, LIBRARY_TABLES)
            for table in LIBRARY_TABLES:
                _copy_table(source, dest, table, dry_run=dry_run, report=report)
        if settings:
            if settings_path is None or not settings_path.is_file():
                report.skipped.append("settings.db (file not found)")
                say("No settings SQLite file — skipping app_settings / academic_sources")
            else:
                say(f"Settings SQLite: {settings_path}")
                settings_engine = _open_sqlite(settings_path)
                try:
                    if not dry_run:
                        _truncate_tables(dest, SETTINGS_TABLES)
                    for table in SETTINGS_TABLES:
                        _copy_table(settings_engine, dest, table, dry_run=dry_run, report=report)
                finally:
                    settings_engine.dispose()
    finally:
        if source is not None:
            source.dispose()
    return report


def default_sqlite_paths() -> tuple[Path, Path]:
    cfg = load_config()
    library = Path(cfg.env.database_path)
    if not library.is_absolute():
        library = ROOT_DIR / library
    settings = Path(cfg.env.settings_sqlite_path or "data/settings.db")
    if not settings.is_absolute():
        settings = ROOT_DIR / settings
    return library, settings


def format_report(report: MigrateReport) -> str:
    prefix = "Would copy" if report.dry_run else "Copied"
    lines = [f"{prefix}:"]
    if report.tables:
        width = max(len(name) for name in report.tables)
        for name, count in report.tables.items():
            lines.append(f"  {name.ljust(width)}  {count:>8}")
    else:
        lines.append("  (no tables)")
    if report.skipped:
        lines.append("Skipped:")
        for item in report.skipped:
            lines.append(f"  {item}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Copy SQLite research.db and settings.db into MySQL (MYSQL_* in .env)."
    )
    parser.add_argument("--sqlite", type=Path, default=None, help="Path to research.db")
    parser.add_argument("--settings-sqlite", type=Path, default=None, help="Path to settings.db")
    parser.add_argument("--replace", action="store_true", help="Wipe MySQL users/papers tables before copy")
    parser.add_argument("--dry-run", action="store_true", help="Count SQLite rows without writing")
    parser.add_argument("--library-only", action="store_true")
    parser.add_argument("--settings-only", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Do not prompt for --replace")
    args = parser.parse_args(argv)

    library_path, settings_path = default_sqlite_paths()
    sqlite_path = (args.sqlite or library_path).expanduser()
    settings_sqlite = (args.settings_sqlite or settings_path).expanduser()
    do_library = not args.settings_only
    do_settings = not args.library_only
    if args.replace and not args.yes and not args.dry_run:
        answer = input("This deletes existing MySQL library rows matching SQLite tables. Continue? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("Aborted.")
            return 1
    report = run_migration(
        sqlite_path,
        settings_sqlite if do_settings else None,
        replace=args.replace,
        dry_run=args.dry_run,
        library=do_library,
        settings=do_settings,
        progress=print,
    )
    print(format_report(report))
    return 0
