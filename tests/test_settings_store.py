from pathlib import Path

from app.config import get_runtime_config, load_config
from app.database.settings_repository import (
    SettingsError,
    create_academic_source,
    delete_academic_source,
    get_academic_source_by_slug,
    list_academic_sources,
    save_workspace_settings,
    toggle_academic_source,
    update_academic_source,
)
from app.database.settings_store import store_status


def test_settings_store_seeds_builtin_sources(tmp_db):
    rows = list_academic_sources()
    slugs = {row.slug for row in rows}
    assert "openalex" in slugs
    assert "crossref" in slugs
    assert "nasa_ntrs" in slugs
    assert "nasa_ads" in slugs
    assert "openaire" in slugs
    assert "zenodo" in slugs
    assert "plos" in slugs
    assert store_status().connected is True
    assert store_status().backend == "sqlite"


def test_seed_inserts_missing_builtin_sources(tmp_db):
    from sqlalchemy import select

    from app.database.settings_models import AcademicSource
    from app.database.settings_repository import seed_academic_sources
    from app.database.settings_store import settings_session

    with settings_session() as session:
        row = session.scalar(select(AcademicSource).where(AcademicSource.slug == "zenodo"))
        assert row is not None
        session.delete(row)

    assert get_academic_source_by_slug("zenodo") is None
    seed_academic_sources()
    restored = get_academic_source_by_slug("zenodo")
    assert restored is not None
    assert restored.builtin is True
    assert restored.display_name == "Zenodo"


def test_sources_page_lists_new_free_providers(tmp_db):
    from fastapi.testclient import TestClient

    from app.web import app

    response = TestClient(app).get("/sources?per_page=50")
    assert response.status_code == 200
    for label in ("OpenAIRE", "Zenodo", "PLOS", "EconStor", "INSPIRE-HEP", "bioRxiv"):
        assert label in response.text
    assert 'role="switch"' in response.text
    assert "source-toggle" in response.text


def test_sources_pagination_and_search(tmp_db):
    from fastapi.testclient import TestClient

    from app.web import app

    client = TestClient(app)
    first = client.get("/sources?per_page=10")
    assert first.status_code == 200
    assert "Showing" in first.text
    assert "1–10" in first.text
    assert 'aria-label="Source pages"' in first.text
    assert "Per page" in first.text
    assert "OpenAlex" in first.text
    assert "EconStor" not in first.text
    second = client.get("/sources?per_page=10&page=2")
    assert second.status_code == 200
    assert "Page 2 of" in second.text
    searched = client.get("/sources?q=EconStor")
    assert searched.status_code == 200
    assert "EconStor" in searched.text
    assert "OpenAlex" not in searched.text
    assert "Nothing matches this filter" not in searched.text
    missing = client.get("/sources?q=not-a-real-source-xyz")
    assert "Nothing matches this filter" in missing.text
    assert 'href="/sources"' in missing.text or "Show all sources" in missing.text



def test_academic_source_crud(tmp_db):
    created = create_academic_source(
        {
            "slug": "custom_repo",
            "display_name": "Custom Repository",
            "description": "User-added catalog entry",
            "homepage_url": "https://example.org",
            "api_base_url": "https://api.example.org",
            "enabled": "1",
            "requires_key": "",
            "requests_per_second": 4,
        }
    )
    assert created.id
    assert created.slug == "custom_repo"
    assert created.builtin is False

    updated = update_academic_source(
        created.id,
        {
            "display_name": "Custom Repository Graph",
            "description": "Updated",
            "homepage_url": "https://example.org",
            "api_base_url": "https://api.example.org",
            "enabled": "1",
            "requests_per_second": 6,
        },
    )
    assert updated.display_name == "Custom Repository Graph"
    assert updated.requests_per_second == 6.0

    toggled = toggle_academic_source(created.id)
    assert toggled.enabled is False

    delete_academic_source(created.id)
    assert get_academic_source_by_slug("custom_repo") is None


def test_cannot_delete_builtin_source(tmp_db):
    builtin = get_academic_source_by_slug("openalex")
    assert builtin is not None
    try:
        delete_academic_source(builtin.id)
        raise AssertionError("builtin delete should fail")
    except SettingsError as exc:
        assert "cannot be deleted" in str(exc)


def test_workspace_settings_overlay(tmp_db):
    save_workspace_settings(
        {
            "contact_email": "lab@university.edu",
            "unpaywall_email": "oa@university.edu",
            "library_dir": "custom_library",
            "check_robots_txt": False,
            "prefer_https": True,
            "timezone": "Asia/Yangon",
        }
    )
    cfg = get_runtime_config()
    assert cfg.env.contact_email == "lab@university.edu"
    assert cfg.env.unpaywall_email == "oa@university.edu"
    assert Path(cfg.library_dir).as_posix().endswith("custom_library")
    assert cfg.check_robots_txt is False
    assert cfg.timezone == "Asia/Yangon"
    assert cfg.show_paywalled is True
    load_config.cache_clear()


def test_hide_paywalled_workspace_setting(tmp_db):
    save_workspace_settings(
        {
            "contact_email": "lab@university.edu",
            "unpaywall_email": "oa@university.edu",
            "library_dir": "custom_library",
            "check_robots_txt": True,
            "prefer_https": True,
            "timezone": "UTC",
            "show_paywalled": False,
        }
    )
    cfg = get_runtime_config()
    assert cfg.show_paywalled is False
    load_config.cache_clear()


def test_disable_source_hides_it_from_runtime(tmp_db):
    openalex = get_academic_source_by_slug("openalex")
    toggle_academic_source(openalex.id, enabled=False)
    cfg = get_runtime_config()
    assert cfg.providers["openalex"].enabled is False
