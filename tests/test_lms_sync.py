from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import load_config
from app.database.connection import session_scope
from app.database.models import Download, LmsExport, Paper, PaperAuthor
from app.database.repository import save_paper
from app.models.paper import AuthorRecord, PaperRecord, PaperStatus
from app.services.lms_sync import (
    LmsSyncConfig,
    build_description,
    discover_lms_root,
    first_author_name,
    infer_category,
    paper_release_date,
    sync_downloaded_papers_to_lms,
)


class FakeCatalog:
    def __init__(self) -> None:
        self.authors: dict[str, int] = {}
        self.categories: dict[str, int] = {}
        self.ebooks: list[dict] = []
        self.next_id = 1

    def find_or_create_author(self, name: str) -> int:
        if name not in self.authors:
            self.authors[name] = self.next_id
            self.next_id += 1
        return self.authors[name]

    def find_or_create_category(self, name: str) -> int:
        if name not in self.categories:
            self.categories[name] = self.next_id
            self.next_id += 1
        return self.categories[name]

    def find_ebook_id(self, *, paper_id: int, doi: str | None, title: str) -> int | None:
        for row in self.ebooks:
            if f"Collector-Paper-ID: {paper_id}" in row["description"]:
                return row["id"]
            if doi and f"DOI: {doi}" in row["description"]:
                return row["id"]
            if row["title"] == title:
                return row["id"]
        return None

    def create_ebook(self, **kwargs) -> int:
        ebook_id = self.next_id
        self.next_id += 1
        self.ebooks.append(
            {
                "id": ebook_id,
                "title": kwargs["title"],
                "description": kwargs["description"],
                "pdf_file": kwargs["pdf_file"],
                "cover_image": kwargs["cover_image"],
                "author_id": kwargs["author_id"],
                "category_id": kwargs["category_id"],
                "release_date": kwargs["release_date"],
            }
        )
        return ebook_id

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def _lms_layout(tmp_path: Path) -> Path:
    root = tmp_path / "library_Management_System" / "library_Management_System"
    root.mkdir(parents=True)
    (root / "package.json").write_text('{"name":"library-management-system"}', encoding="utf-8")
    (root / "server.js").write_text("module.exports = {}", encoding="utf-8")
    (root / "models").mkdir()
    (root / "public" / "uploads" / "eBooks").mkdir(parents=True)
    (root / "public" / "uploads" / "covers").mkdir(parents=True)
    return root


MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 200 200]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
    b"0000000052 00000 n \n0000000101 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n"
)


def _write_pdf(path: Path, _title: str = "Hello") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MINIMAL_PDF)


def _cfg(root: Path, default_category: str = "Research Papers") -> LmsSyncConfig:
    return LmsSyncConfig(
        enabled=True,
        root=root,
        default_category=default_category,
        db_host="127.0.0.1",
        db_port=3306,
        db_name="library",
        db_user="user",
        db_password="",
    )


def test_discover_lms_root(tmp_path, monkeypatch):
    monkeypatch.setenv("LMS_ROOT", "")
    load_config.cache_clear()
    root = _lms_layout(tmp_path)
    found = discover_lms_root(tmp_path)
    assert found == root.resolve()


def test_discover_elibrary_sibling(tmp_path, monkeypatch):
    monkeypatch.setenv("LMS_ROOT", "")
    load_config.cache_clear()
    root = tmp_path / "elibrary"
    root.mkdir()
    (root / "package.json").write_text('{"name":"library-management-system"}', encoding="utf-8")
    (root / "server.js").write_text("module.exports = {}", encoding="utf-8")
    (root / "models").mkdir()
    found = discover_lms_root(tmp_path)
    assert found == root.resolve()


def test_discover_lms_root_from_env(tmp_path, monkeypatch):
    root = tmp_path / "elibrary"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    (root / "server.js").write_text("", encoding="utf-8")
    (root / "models").mkdir()
    monkeypatch.setenv("LMS_ROOT", str(root))
    load_config.cache_clear()
    found = discover_lms_root(tmp_path)
    assert found == root.resolve()


def test_metadata_helpers(tmp_db):
    record = PaperRecord(
        title="Deep learning for intrusion detection",
        abstract="A survey.",
        doi="10.1000/ids",
        arxiv_id="2401.00001",
        journal="Computers & Security",
        source_provider="arxiv",
        publication_year=2024,
        publication_date="2024-06",
        research_fields=["Cybersecurity", "Machine Learning"],
        authors=[AuthorRecord(name="Ada Lovelace")],
        status=PaperStatus.DOWNLOADED,
    )
    with session_scope() as session:
        paper = save_paper(session, record)
        session.flush()
        paper = session.scalar(
            select(Paper)
            .options(selectinload(Paper.authors).selectinload(PaperAuthor.author))
            .where(Paper.id == paper.id)
        )
        assert paper is not None
        assert first_author_name(paper) == "Ada Lovelace"
        assert infer_category(paper, "Research Papers") == "Cybersecurity"
        assert paper_release_date(paper) == "2024-06-01"
        description = build_description(paper)
        assert "DOI: 10.1000/ids" in description
        assert f"Collector-Paper-ID: {paper.id}" in description
        assert "A survey." in description


def test_sync_copies_pdf_and_creates_ebook(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setenv("LMS_ROOT", "")
    load_config.cache_clear()
    lms_root = _lms_layout(tmp_path)
    pdf_path = tmp_path / "research_library" / "topic" / "paper.pdf"
    _write_pdf(pdf_path, "Intrusion detection")

    record = PaperRecord(
        title="A method for web application security",
        abstract="We propose a detector.",
        doi="10.1000/websec",
        authors=[AuthorRecord(name="Jane Smith")],
        publication_year=2025,
        research_fields=["Cybersecurity"],
        status=PaperStatus.DOWNLOADED,
        extra={"local_path": str(pdf_path)},
    )
    with session_scope() as session:
        paper = save_paper(session, record)
        session.add(
            Download(
                paper_id=paper.id,
                local_path=str(pdf_path),
                status=PaperStatus.DOWNLOADED.value,
            )
        )
        paper_id = paper.id

    catalog = FakeCatalog()
    result = sync_downloaded_papers_to_lms(
        paper_ids=[paper_id],
        catalog=catalog,
        config=_cfg(lms_root),
    )
    assert result.imported == 1
    assert result.failed == 0
    assert len(catalog.ebooks) == 1
    ebook = catalog.ebooks[0]
    assert ebook["title"] == "A method for web application security"
    assert "Jane Smith" in catalog.authors
    assert "Cybersecurity" in catalog.categories
    copied = list((lms_root / "public" / "uploads" / "eBooks").glob("*.pdf"))
    assert len(copied) == 1
    assert copied[0].stat().st_size > 0

    with session_scope() as session:
        exported = session.scalar(select(LmsExport).where(LmsExport.paper_id == paper_id))
        assert exported is not None
        assert exported.status == "imported"
        assert exported.ebook_id == ebook["id"]

    again = sync_downloaded_papers_to_lms(
        paper_ids=[paper_id],
        catalog=catalog,
        config=_cfg(lms_root),
    )
    assert again.skipped == 1
    assert len(catalog.ebooks) == 1


def test_schedule_lms_sync_is_non_blocking(tmp_db, monkeypatch):
    monkeypatch.setenv("LMS_SYNC_ENABLED", "false")
    load_config.cache_clear()
    from app.services.lms_watch import schedule_lms_sync, stop_lms_watch

    schedule_lms_sync(paper_ids=[1])
    stop_lms_watch()


def test_sync_skips_when_pdf_missing(tmp_db, tmp_path):
    lms_root = _lms_layout(tmp_path)
    record = PaperRecord(
        title="Missing file",
        authors=[AuthorRecord(name="Anon")],
        status=PaperStatus.DOWNLOADED,
    )
    with session_scope() as session:
        paper = save_paper(session, record)
        session.add(
            Download(
                paper_id=paper.id,
                local_path=str(tmp_path / "gone.pdf"),
                status=PaperStatus.DOWNLOADED.value,
            )
        )
        paper_id = paper.id

    catalog = FakeCatalog()
    result = sync_downloaded_papers_to_lms(
        paper_ids=[paper_id],
        catalog=catalog,
        config=_cfg(lms_root),
    )
    assert result.imported == 0
    assert result.skipped == 1
    assert catalog.ebooks == []
