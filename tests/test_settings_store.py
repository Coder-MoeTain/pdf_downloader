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
    assert store_status().connected is True
    assert store_status().backend == "sqlite"


def test_academic_source_crud(tmp_db):
    created = create_academic_source(
        {
            "slug": "openaire",
            "display_name": "OpenAIRE",
            "description": "European open science graph",
            "homepage_url": "https://www.openaire.eu",
            "api_base_url": "https://api.openaire.eu",
            "enabled": "1",
            "requires_key": "",
            "requests_per_second": 4,
        }
    )
    assert created.id
    assert created.slug == "openaire"
    assert created.builtin is False

    updated = update_academic_source(
        created.id,
        {
            "display_name": "OpenAIRE Graph",
            "description": "Updated",
            "homepage_url": "https://www.openaire.eu",
            "api_base_url": "https://api.openaire.eu",
            "enabled": "1",
            "requests_per_second": 6,
        },
    )
    assert updated.display_name == "OpenAIRE Graph"
    assert updated.requests_per_second == 6.0

    toggled = toggle_academic_source(created.id)
    assert toggled.enabled is False

    delete_academic_source(created.id)
    assert get_academic_source_by_slug("openaire") is None


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
