from pathlib import Path

from app.utils.filename import normalize_title, paper_filename, safe_join, sanitize_component, slugify
import pytest


def test_normalize_title_collapses_punctuation():
    a = normalize_title("Machine-Learning Based Detection of SQL Injection Attacks!")
    b = normalize_title("machine learning based detection of sql injection attacks")
    assert a == b


def test_sanitize_and_slug():
    assert ":" not in sanitize_component('bad:name*?')
    assert slugify("Web Application Security") == "web_application_security"


def test_paper_filename_windows_safe():
    name = paper_filename(2025, "Jane Smith", "Autonomous Penetration Testing", "10.1234/abc")
    assert name.startswith("2025_Smith_")
    assert name.endswith(".pdf")
    assert "/" not in name
    assert ":" not in name
    assert len(name) < 160


def test_safe_join_blocks_traversal(tmp_path: Path):
    base = tmp_path / "lib"
    base.mkdir()
    ok = safe_join(base, "topic", "2024")
    assert str(ok).startswith(str(base.resolve()))
    with pytest.raises(ValueError):
        safe_join(base, "..", "..", "Windows")
