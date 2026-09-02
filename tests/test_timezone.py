from datetime import datetime

from app.config import get_runtime_config
from app.database.settings_repository import SettingsError, save_workspace_settings
from app.utils.time import format_local, normalize_timezone, to_local, utc_now


def test_yangon_converts_from_utc():
    noon_utc = datetime(2026, 9, 2, 12, 0, 0)
    local = to_local(noon_utc, "Asia/Yangon")
    assert local is not None
    assert local.hour == 18
    assert local.minute == 30
    assert format_local(noon_utc, "%H:%M", tz_name="Asia/Yangon") == "18:30"


def test_normalize_rejects_unknown_zone():
    try:
        normalize_timezone("Not/A_Zone")
        raise AssertionError("invalid timezone should fail")
    except ValueError as exc:
        assert "Unknown timezone" in str(exc)


def test_workspace_timezone_overlay(tmp_db):
    save_workspace_settings(
        {
            "contact_email": "lab@university.edu",
            "unpaywall_email": "",
            "library_dir": "research_library",
            "timezone": "Asia/Yangon",
            "check_robots_txt": True,
            "prefer_https": True,
        }
    )
    cfg = get_runtime_config()
    assert cfg.timezone == "Asia/Yangon"
    stamp = format_local(utc_now(), "%Y-%m-%d %H:%M")
    assert stamp


def test_invalid_timezone_is_rejected(tmp_db):
    try:
        save_workspace_settings(
            {
                "contact_email": "lab@university.edu",
                "library_dir": "research_library",
                "timezone": "Mars/Olympus",
            }
        )
        raise AssertionError("invalid timezone should fail")
    except SettingsError:
        pass
