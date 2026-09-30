from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database.models import Author, Base, Paper, PaperAuthor, User
from app.database.settings_models import AcademicSource, AppSetting, SettingsBase
from app.database.sqlite_to_mysql import _coerce, _declared_length, _mysql_string_kind, run_migration
from app.utils.time import utc_now


def _engine(path: Path):
    engine = create_engine(f"sqlite:///{path.as_posix()}", future=True)
    Base.metadata.create_all(engine)
    SettingsBase.metadata.create_all(engine)
    return engine


def test_migrate_copies_library_and_settings(tmp_path):
    src_lib = tmp_path / "research.db"
    src_settings = tmp_path / "settings.db"
    dest_path = tmp_path / "dest.db"
    src_engine = _engine(src_lib)
    settings_engine = create_engine(f"sqlite:///{src_settings.as_posix()}", future=True)
    SettingsBase.metadata.create_all(settings_engine)
    dest_engine = _engine(dest_path)

    now = utc_now()
    with Session(src_engine) as session:
        user = User(
            id=7,
            google_id="local:admin@example.com",
            email="admin@example.com",
            name="Admin",
            role="admin",
            is_admin=True,
            last_login_at=now,
            created_at=now,
        )
        author = Author(id=3, name="Ada Lovelace", normalized_name="ada lovelace")
        paper = Paper(id=11, title="Notes on the Analytical Engine", normalized_title="notes on the analytical engine")
        session.add_all([user, author, paper])
        session.flush()
        session.add(PaperAuthor(paper_id=paper.id, author_id=author.id, position=0))
        session.commit()
    with Session(settings_engine) as session:
        session.add(AppSetting(key="timezone", value="Asia/Yangon", group_name="workspace"))
        session.add(
            AcademicSource(
                id=4,
                slug="arxiv",
                display_name="arXiv",
                enabled=True,
                builtin=True,
                sort_order=1,
            )
        )
        session.commit()
    src_engine.dispose()
    settings_engine.dispose()

    report = run_migration(
        src_lib,
        src_settings,
        dest_engine=dest_engine,
        replace=True,
    )
    assert report.tables["papers"] == 1
    assert report.tables["users"] == 1
    assert report.tables["academic_sources"] == 1

    with Session(dest_engine) as session:
        paper = session.get(Paper, 11)
        user = session.get(User, 7)
        source = session.get(AcademicSource, 4)
        setting = session.get(AppSetting, "timezone")
        author_link = session.scalar(select(PaperAuthor))
        assert paper is not None and paper.title.startswith("Notes")
        assert user is not None and user.email == "admin@example.com" and user.is_admin
        assert source is not None and source.slug == "arxiv"
        assert setting is not None and setting.value == "Asia/Yangon"
        assert author_link is not None and author_link.paper_id == 11 and author_link.author_id == 3
    dest_engine.dispose()


def test_migrate_refuses_occupied_destination(tmp_path):
    src = tmp_path / "research.db"
    dest = tmp_path / "dest.db"
    src_engine = _engine(src)
    dest_engine = _engine(dest)
    with Session(src_engine) as session:
        session.add(Paper(title="From SQLite", normalized_title="from sqlite"))
        session.commit()
    with Session(dest_engine) as session:
        session.add(
            User(
                google_id="already",
                email="existing@example.com",
                name="Existing",
                last_login_at=utc_now(),
                created_at=utc_now(),
            )
        )
        session.commit()
    src_engine.dispose()
    try:
        run_migration(src, None, dest_engine=dest_engine, replace=False, settings=False)
        raise AssertionError("expected occupied destination to fail")
    except RuntimeError as exc:
        assert "already has rows" in str(exc)
    dest_engine.dispose()


def test_migrate_dry_run_does_not_write(tmp_path):
    src = tmp_path / "research.db"
    dest = tmp_path / "dest.db"
    src_engine = _engine(src)
    dest_engine = _engine(dest)
    with Session(src_engine) as session:
        session.add(Paper(title="Dry", normalized_title="dry"))
        session.commit()
    src_engine.dispose()
    report = run_migration(src, None, dest_engine=dest_engine, dry_run=True, settings=False)
    assert report.tables["papers"] == 1
    with Session(dest_engine) as session:
        assert session.scalar(select(Paper)) is None
    dest_engine.dispose()


def test_declared_length_and_varchar_truncate():
    assert _declared_length("VARCHAR(512)") == 512
    assert _declared_length("TEXT") is None
    assert _mysql_string_kind("VARCHAR(512)") == "varchar"
    assert _mysql_string_kind("TEXT") == "text"
    assert _mysql_string_kind("LONGTEXT") == "longtext"
    long_name = "x" * 600
    assert _coerce(long_name, "VARCHAR(512)") == "x" * 512
    assert _coerce(long_name, "TEXT") == long_name
    assert _coerce("y" * 70_000, "LONGTEXT") == "y" * 70_000


def test_migrate_copies_long_author_name(tmp_path):
    src = tmp_path / "research.db"
    dest = tmp_path / "dest.db"
    src_engine = _engine(src)
    dest_engine = _engine(dest)
    long_name = "Consortium " + ("Member, " * 80) + "Last"
    with Session(src_engine) as session:
        session.add(Author(id=1, name=long_name, normalized_name=long_name.lower()))
        session.commit()
    src_engine.dispose()
    run_migration(src, None, dest_engine=dest_engine, replace=True, settings=False)
    with Session(dest_engine) as session:
        author = session.get(Author, 1)
        assert author is not None
        assert author.name == long_name
    dest_engine.dispose()
