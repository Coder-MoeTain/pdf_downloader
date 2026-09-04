"""Copy downloaded open-access papers into the sibling Library Management System."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import ROOT_DIR, get_runtime_config
from app.database.connection import session_scope
from app.database.models import Download, LmsExport, Paper, PaperAuthor
from app.models.paper import PaperStatus
from app.utils.filename import sanitize_component
from app.utils.logger import get_logger
from app.utils.time import utc_now

logger = get_logger("app.lms_sync")

PAPER_ID_MARKER = "Collector-Paper-ID:"
DEFAULT_CATEGORY = "Research Papers"
MAX_TITLE_LEN = 255
MAX_AUTHOR_LEN = 255
MAX_CATEGORY_LEN = 255

CATEGORY_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("Cybersecurity", re.compile(r"\b(security|cyber|intrusion|malware|vulnerability|cryptograph|penetration|ids|ips)\b", re.I)),
    ("Artificial Intelligence", re.compile(r"\b(artificial intelligence|machine learning|deep learning|neural|llm|reinforcement)\b", re.I)),
    ("Computer Science", re.compile(r"\b(computer science|algorithm|software|programming|distributed|database)\b", re.I)),
    ("Engineering", re.compile(r"\b(engineering|satellite|aerospace|network)\b", re.I)),
    ("Mathematics", re.compile(r"\b(mathematics|statistical|optimization)\b", re.I)),
]


@dataclass
class LmsSyncConfig:
    enabled: bool
    root: Path | None
    default_category: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str

    @property
    def uploads_root(self) -> Path:
        assert self.root is not None
        return self.root / "public" / "uploads"

    @property
    def ebook_dir(self) -> Path:
        return self.uploads_root / "eBooks"

    @property
    def cover_dir(self) -> Path:
        return self.uploads_root / "covers"


@dataclass
class SyncResult:
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    messages: list[str] = field(default_factory=list)

    def add(self, line: str) -> None:
        self.messages.append(line)
        logger.info(line)


class LmsCatalog(Protocol):
    def find_or_create_author(self, name: str) -> int: ...
    def find_or_create_category(self, name: str) -> int: ...
    def find_ebook_id(self, *, paper_id: int, doi: str | None, title: str) -> int | None: ...
    def create_ebook(
        self,
        *,
        title: str,
        release_date: str | None,
        description: str,
        pdf_file: str,
        cover_image: str | None,
        author_id: int,
        category_id: int,
    ) -> int: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


def discover_lms_root(start: Path | None = None) -> Path | None:
    """Find the e-library app next to this project (same parent folder on the server)."""
    configured = (get_runtime_config().env.lms_root or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = (start or ROOT_DIR) / path
        return _find_lms_in(path)

    bases = [start or ROOT_DIR.parent, ROOT_DIR.parent]
    names = (
        "elibrary",
        "e-library",
        "e_library",
        "library_Management_System",
        "library_management_system",
    )
    seen: set[Path] = set()
    for base in bases:
        for name in names:
            found = _find_lms_in(base / name)
            if found and found not in seen:
                return found
            if found:
                seen.add(found)
        found = _scan_sibling_lms(base, seen)
        if found:
            return found
    return None


def _looks_like_lms(path: Path) -> bool:
    return (path / "package.json").is_file() and (path / "models").is_dir() and (path / "server.js").is_file()


def _find_lms_in(path: Path) -> Path | None:
    """Accept the app root, or one nested folder (e.g. elibrary/library_Management_System)."""
    if _looks_like_lms(path):
        return path.resolve()
    if not path.is_dir():
        return None
    try:
        children = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return None
    for child in children:
        if child.is_dir() and _looks_like_lms(child):
            return child.resolve()
    return None


def _scan_sibling_lms(base: Path, seen: set[Path]) -> Path | None:
    if not base.is_dir():
        return None
    try:
        children = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return None
    for child in children:
        if not child.is_dir() or child.name in {".", "..", "html", "pdf_downloader"}:
            continue
        found = _find_lms_in(child)
        if found and found not in seen:
            return found
    return None


def load_lms_sync_config(start: Path | None = None) -> LmsSyncConfig:
    env = get_runtime_config().env
    root = discover_lms_root(start)
    lms_env: dict[str, str] = {}
    if root is not None:
        env_file = root / ".env"
        if env_file.is_file():
            lms_env = {k: v for k, v in dotenv_values(env_file).items() if k and v is not None}

    port_raw = env.lms_db_port or int(lms_env.get("DB_PORT") or 3306)
    return LmsSyncConfig(
        enabled=bool(env.lms_sync_enabled),
        root=root,
        default_category=(env.lms_category or lms_env.get("LMS_CATEGORY") or DEFAULT_CATEGORY).strip()
        or DEFAULT_CATEGORY,
        db_host=(env.lms_db_host or lms_env.get("DB_HOST") or "127.0.0.1").strip(),
        db_port=int(port_raw),
        db_name=(env.lms_db_name or lms_env.get("DB_NAME") or "library").strip(),
        db_user=(env.lms_db_user or lms_env.get("DB_USER") or "root").strip(),
        db_password=env.lms_db_password or lms_env.get("DB_PASSWORD") or "",
    )


def paper_marker_in_description(description: str | None, paper_id: int) -> bool:
    """True only for this paper id — `Collector-Paper-ID: 12` must not match 120."""
    return bool(
        re.search(
            rf"{re.escape(PAPER_ID_MARKER)}\s+{int(paper_id)}(?!\d)",
            description or "",
        )
    )


def doi_in_description(description: str | None, doi: str | None) -> bool:
    value = (doi or "").strip()
    if not value:
        return False
    return bool(re.search(rf"(?im)^DOI:\s*{re.escape(value)}\s*$", description or ""))


def first_author_name(paper: Paper) -> str:
    links = sorted(paper.authors or [], key=lambda row: row.position)
    for link in links:
        name = (link.author.name if link.author else "").strip()
        if name:
            return name[:MAX_AUTHOR_LEN]
    return "Unknown Author"


def infer_category(paper: Paper, default_category: str) -> str:
    fields = [part.strip() for part in (paper.research_fields or "").split(";") if part.strip()]
    if fields:
        return fields[0][:MAX_CATEGORY_LEN]
    haystack = " ".join(
        part
        for part in (paper.title, paper.keywords, paper.research_fields, paper.journal)
        if part
    )
    for name, pattern in CATEGORY_RULES:
        if pattern.search(haystack):
            return name
    return default_category[:MAX_CATEGORY_LEN]


def paper_release_date(paper: Paper) -> str | None:
    raw = (paper.publication_date or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    if re.fullmatch(r"\d{4}-\d{2}", raw):
        return f"{raw}-01"
    if paper.publication_year:
        return f"{int(paper.publication_year):04d}-01-01"
    return None


def build_description(paper: Paper) -> str:
    lines: list[str] = []
    abstract = (paper.abstract or "").strip()
    if abstract:
        lines.append(abstract)
    venue = paper.journal or paper.conference
    if venue:
        lines.append(f"Journal: {venue}")
    if paper.doi:
        lines.append(f"DOI: {paper.doi}")
    if paper.arxiv_id:
        lines.append(f"arXiv: {paper.arxiv_id}")
    if paper.source:
        lines.append(f"Source: {paper.source}")
    lines.append(f"{PAPER_ID_MARKER} {paper.id}")
    return "\n".join(lines).strip()


def existing_pdf_for_paper(paper: Paper) -> Path | None:
    for row in paper.downloads or []:
        if not row.local_path:
            continue
        if row.status not in {PaperStatus.DOWNLOADED.value, PaperStatus.DUPLICATE.value}:
            continue
        path = Path(row.local_path)
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def render_cover(pdf_path: Path, dest: Path) -> bool:
    try:
        import fitz
    except ImportError:
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(pdf_path)
        try:
            if doc.page_count < 1:
                return False
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            dest.write_bytes(pix.tobytes("png"))
        finally:
            doc.close()
        return dest.is_file() and dest.stat().st_size > 0
    except Exception as exc:
        logger.warning("Cover render failed for %s: %s", pdf_path.name, exc)
        return False


class MysqlLmsCatalog:
    def __init__(self, cfg: LmsSyncConfig) -> None:
        import pymysql

        self.conn = pymysql.connect(
            host=cfg.db_host,
            port=cfg.db_port,
            user=cfg.db_user,
            password=cfg.db_password,
            database=cfg.db_name,
            charset="utf8mb4",
            autocommit=False,
        )

    def find_or_create_author(self, name: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT Author_id FROM author WHERE Author_name = %s LIMIT 1", (name,))
            row = cur.fetchone()
            if row:
                return int(row[0])
            cur.execute(
                "INSERT INTO author (Author_name, created_at, updated_at) VALUES (%s, NOW(), NOW())",
                (name,),
            )
            return int(cur.lastrowid)

    def find_or_create_category(self, name: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT category_id FROM category WHERE category_name = %s LIMIT 1", (name,))
            row = cur.fetchone()
            if row:
                return int(row[0])
            cur.execute(
                "INSERT INTO category (category_name, created_at, updated_at) VALUES (%s, NOW(), NOW())",
                (name,),
            )
            return int(cur.lastrowid)

    def find_ebook_id(self, *, paper_id: int, doi: str | None, title: str) -> int | None:
        del title  # title-only match caused unrelated catalog rows to block imports
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT eBooks_id, description FROM ebooks WHERE description LIKE %s",
                (f"%{PAPER_ID_MARKER} {paper_id}%",),
            )
            for ebook_id, description in cur.fetchall():
                if paper_marker_in_description(description, paper_id):
                    return int(ebook_id)
            if doi:
                cur.execute(
                    "SELECT eBooks_id, description FROM ebooks WHERE description LIKE %s",
                    (f"%DOI: {doi}%",),
                )
                for ebook_id, description in cur.fetchall():
                    if doi_in_description(description, doi):
                        return int(ebook_id)
        return None

    def create_ebook(
        self,
        *,
        title: str,
        release_date: str | None,
        description: str,
        pdf_file: str,
        cover_image: str | None,
        author_id: int,
        category_id: int,
    ) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ebooks (
                    eBook_name, release_date, description, cover_image, pdf_file,
                    Category_category_id, Author_Author_id, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (title, release_date, description, cover_image, pdf_file, category_id, author_id),
            )
            ebook_id = int(cur.lastrowid)
        self.conn.commit()
        return ebook_id

    def rollback(self) -> None:
        try:
            self.conn.rollback()
        except Exception:
            pass

    def close(self) -> None:
        self.conn.close()


def _record_export(paper_id: int, ebook_id: int | None, status: str, error: str | None = None) -> None:
    with session_scope() as session:
        row = session.scalar(select(LmsExport).where(LmsExport.paper_id == paper_id))
        if row is None:
            row = LmsExport(paper_id=paper_id)
            session.add(row)
        row.ebook_id = ebook_id
        row.status = status
        row.error_message = error
        row.synced_at = utc_now()


def _load_papers(paper_ids: list[int] | None) -> list[Paper]:
    """Load downloaded papers. Always re-check LMS: a prior false-positive skip must retry."""
    with session_scope() as session:
        stmt = (
            select(Paper)
            .options(
                selectinload(Paper.authors).selectinload(PaperAuthor.author),
                selectinload(Paper.downloads),
            )
            .join(Download, Download.paper_id == Paper.id)
            .where(
                Download.local_path.is_not(None),
                Download.local_path != "",
                Download.status.in_([PaperStatus.DOWNLOADED.value, PaperStatus.DUPLICATE.value]),
            )
        )
        if paper_ids:
            stmt = stmt.where(Paper.id.in_(paper_ids))
        return list(session.scalars(stmt).unique().all())


def _safe_stem(title: str, paper_id: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    part = sanitize_component(title, max_length=80, fallback="paper")
    return f"{stamp}-{paper_id}-{part}"


def import_paper(paper: Paper, cfg: LmsSyncConfig, catalog: LmsCatalog | None, *, dry_run: bool) -> str:
    pdf_src = existing_pdf_for_paper(paper)
    if pdf_src is None:
        return "skipped: PDF missing on disk"

    title = (paper.title or pdf_src.stem).strip()[:MAX_TITLE_LEN] or f"Paper {paper.id}"
    author = first_author_name(paper)
    category = infer_category(paper, cfg.default_category)
    description = build_description(paper)
    released = paper_release_date(paper)

    if dry_run:
        return f"dry-run: {title} | {author} | {category}"

    if catalog is None:
        raise RuntimeError("LMS catalog is required unless --dry-run")

    existing_id = catalog.find_ebook_id(paper_id=paper.id, doi=paper.doi, title=title)
    if existing_id:
        _record_export(paper.id, existing_id, "imported")
        return f"skipped: already in LMS as e-book {existing_id}"

    cfg.ebook_dir.mkdir(parents=True, exist_ok=True)
    cfg.cover_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(title, paper.id)
    pdf_name = f"{stem}.pdf"
    cover_name = f"{stem}-cover.png"
    pdf_dest = cfg.ebook_dir / pdf_name
    cover_dest = cfg.cover_dir / cover_name
    shutil.copy2(pdf_src, pdf_dest)

    cover_url = None
    if render_cover(pdf_src, cover_dest):
        cover_url = f"/uploads/covers/{cover_name}"

    author_id = catalog.find_or_create_author(author)
    category_id = catalog.find_or_create_category(category)
    ebook_id = catalog.create_ebook(
        title=title,
        release_date=released,
        description=description,
        pdf_file=f"/uploads/eBooks/{pdf_name}",
        cover_image=cover_url,
        author_id=author_id,
        category_id=category_id,
    )
    _record_export(paper.id, ebook_id, "imported")
    return f"imported: {title} (e-book {ebook_id})"


def sync_downloaded_papers_to_lms(
    *,
    paper_ids: list[int] | None = None,
    dry_run: bool = False,
    catalog: LmsCatalog | None = None,
    config: LmsSyncConfig | None = None,
) -> SyncResult:
    result = SyncResult()
    cfg = config or load_lms_sync_config()
    if not cfg.enabled:
        result.add("LMS sync disabled (LMS_SYNC_ENABLED=false)")
        return result
    if cfg.root is None:
        result.add("LMS sync skipped: library project not found next to pdf_downloader")
        return result

    papers = _load_papers(paper_ids)
    if not papers:
        result.add("No downloaded papers with a local PDF record to add to the library")
        return result
    result.add(f"Checking {len(papers)} downloaded paper(s)")

    owned = False
    if catalog is None and not dry_run:
        catalog = MysqlLmsCatalog(cfg)
        owned = True
    try:
        for paper in papers:
            try:
                message = import_paper(paper, cfg, catalog, dry_run=dry_run)
            except Exception as exc:
                result.failed += 1
                if catalog is not None:
                    catalog.rollback()
                result.add(f"failed: {(paper.title or '')[:80]} ({exc})")
                if not dry_run:
                    _record_export(paper.id, None, "failed", str(exc))
                continue
            if message.startswith("imported"):
                result.imported += 1
            elif message.startswith("dry-run"):
                result.imported += 1
            else:
                result.skipped += 1
            result.add(message)
    finally:
        if owned and catalog is not None:
            catalog.close()
    return result


def maybe_sync_to_lms(*, paper_ids: list[int] | None = None) -> SyncResult | None:
    """Best-effort sync after search/download. Never raises into the caller."""
    try:
        cfg = load_lms_sync_config()
        if not cfg.enabled or cfg.root is None:
            if cfg.enabled and cfg.root is None:
                logger.info("LMS sync skipped: set LMS_ROOT or place library_Management_System beside pdf_downloader")
            return None
        if paper_ids is not None and not paper_ids:
            return None
        result = sync_downloaded_papers_to_lms(paper_ids=paper_ids, config=cfg)
        if result.imported or result.failed:
            logger.info(
                "LMS sync finished: imported=%s skipped=%s failed=%s",
                result.imported,
                result.skipped,
                result.failed,
            )
        return result
    except Exception as exc:
        logger.warning("LMS sync failed: %s", exc)
        return None
