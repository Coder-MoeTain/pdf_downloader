"""Top GitHub projects page and repository parsing."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.database.connection import session_scope
from app.database.repository import list_github_repos, replace_github_category_repos
from app.services.github_service import (
    GITHUB_CATEGORIES,
    format_star_count,
    is_framework_or_library,
    merge_project_repos,
    parse_search_items,
    project_name,
    refresh_github_repos,
)
from app.web import app
from tests.conftest import login_admin


def test_format_star_count():
    assert format_star_count(12) == "12"
    assert format_star_count(1500) == "1.5k"
    assert format_star_count(1200) == "1.2k"
    assert format_star_count(1_000_000) == "1M"


def test_parse_search_items_keeps_github_urls_only():
    payload = {
        "items": [
            {
                "full_name": "microsoft/ML-For-Beginners",
                "name": "ML-For-Beginners",
                "description": "12 weeks, 26 lessons, classic Machine Learning for all",
                "html_url": "https://github.com/microsoft/ML-For-Beginners",
                "homepage": "https://microsoft.github.io/ML-For-Beginners/",
                "language": "Jupyter Notebook",
                "stargazers_count": 90000,
                "forks_count": 22000,
                "topics": ["machine-learning", "education"],
                "owner": {"login": "microsoft", "avatar_url": "https://avatars.githubusercontent.com/u/1"},
            },
            {
                "full_name": "evil/repo",
                "html_url": "https://evil.example/repo",
                "stargazers_count": 9,
            },
        ]
    }
    rows = parse_search_items(payload)
    assert len(rows) == 1
    assert rows[0]["full_name"] == "microsoft/ML-For-Beginners"
    assert rows[0]["stars"] == 90000
    assert "education" in (rows[0]["topics"] or "")


def test_parse_search_items_skips_frameworks_and_libraries():
    payload = {
        "items": [
            {
                "full_name": "huggingface/transformers",
                "name": "transformers",
                "description": "State-of-the-art ML",
                "html_url": "https://github.com/huggingface/transformers",
                "stargazers_count": 140000,
                "topics": ["machine-learning", "nlp"],
            },
            {
                "full_name": "tensorflow/tensorflow",
                "name": "tensorflow",
                "description": "An Open Source Machine Learning Framework for Everyone",
                "html_url": "https://github.com/tensorflow/tensorflow",
                "stargazers_count": 200000,
                "topics": ["machine-learning"],
            },
            {
                "full_name": "org/cool-toolkit",
                "name": "cool-toolkit",
                "description": "A Python library for charts",
                "html_url": "https://github.com/org/cool-toolkit",
                "stargazers_count": 12000,
                "topics": ["data-science"],
            },
            {
                "full_name": "microsoft/ML-For-Beginners",
                "name": "ML-For-Beginners",
                "description": "12 weeks of classic Machine Learning",
                "html_url": "https://github.com/microsoft/ML-For-Beginners",
                "stargazers_count": 90000,
                "topics": ["machine-learning"],
            },
        ]
    }
    rows = parse_search_items(payload)
    assert [row["full_name"] for row in rows] == ["microsoft/ML-For-Beginners"]
    assert is_framework_or_library({"name": "pytorch", "description": "", "topics": []})
    assert is_framework_or_library({"name": "bert", "description": "", "topics": []})
    assert is_framework_or_library({"name": "streamlit", "description": "", "topics": []})
    assert project_name(rows[0]) == "ML-For-Beginners"


def test_replace_github_category_repos(tmp_db):
    with session_scope() as session:
        saved = replace_github_category_repos(
            session,
            "machine-learning",
            [
                {
                    "full_name": "huggingface/transformers",
                    "name": "transformers",
                    "description": "ML library",
                    "html_url": "https://github.com/huggingface/transformers",
                    "language": "Python",
                    "stars": 100,
                    "forks": 10,
                    "owner_login": "huggingface",
                },
                {
                    "full_name": "scikit-learn/scikit-learn",
                    "html_url": "https://github.com/scikit-learn/scikit-learn",
                    "stars": 90,
                    "forks": 8,
                },
            ],
        )
        assert saved == 2
    with session_scope() as session:
        rows = list_github_repos(session, category="machine-learning")
        assert [row.full_name for row in rows] == [
            "huggingface/transformers",
            "scikit-learn/scikit-learn",
        ]
        assert rows[0].rank == 1
        replace_github_category_repos(
            session,
            "machine-learning",
            [
                {
                    "full_name": "pytorch/pytorch",
                    "html_url": "https://github.com/pytorch/pytorch",
                    "stars": 80,
                    "forks": 5,
                }
            ],
        )
        rows = list_github_repos(session, category="machine-learning")
        assert [row.full_name for row in rows] == ["pytorch/pytorch"]


def test_refresh_github_repos_persists_top_ten(tmp_db, monkeypatch):
    monkeypatch.setattr("app.services.github_service.time.sleep", lambda _seconds: None)

    def fake_search(topic: str):
        return [
            {
                "full_name": f"org/{topic}-repo",
                "name": f"{topic}-repo",
                "description": f"Top {topic}",
                "html_url": f"https://github.com/org/{topic}-repo",
                "language": "Python",
                "stars": 50,
                "forks": 2,
                "owner_login": "org",
            }
        ]

    monkeypatch.setattr("app.services.github_service._search_topic", fake_search)
    stats = refresh_github_repos()
    assert stats["categories"] == len(GITHUB_CATEGORIES)
    assert stats["repos"] == len(GITHUB_CATEGORIES)
    with session_scope() as session:
        rows = list_github_repos(session)
        assert len(rows) == len(GITHUB_CATEGORIES)
        assert rows[0].html_url.startswith("https://github.com/")


def test_merge_project_repos_dedupes_and_sorts_by_stars():
    merged = merge_project_repos(
        [
            {"full_name": "org/alpha", "name": "alpha", "stars": 10, "category": "nlp"},
            {"full_name": "org/beta", "name": "beta", "stars": 50, "category": "cybersecurity"},
            {"full_name": "org/alpha", "name": "alpha", "stars": 12, "category": "machine-learning"},
        ]
    )
    names = [row["full_name"] for row, _cats in merged]
    assert names == ["org/beta", "org/alpha"]
    assert merged[1][1] == ("nlp", "machine-learning")


def test_projects_page_filters_by_category(tmp_db):
    with session_scope() as session:
        replace_github_category_repos(
            session,
            "machine-learning",
            [
                {
                    "full_name": "huggingface/transformers",
                    "html_url": "https://github.com/huggingface/transformers",
                    "description": "Transformers library",
                    "stars": 2000,
                    "forks": 200,
                    "language": "Python",
                },
                {
                    "full_name": "microsoft/ML-For-Beginners",
                    "name": "ML-For-Beginners",
                    "html_url": "https://github.com/microsoft/ML-For-Beginners",
                    "description": "12 weeks of classic Machine Learning",
                    "stars": 1000,
                    "forks": 100,
                    "language": "Jupyter Notebook",
                    "owner_login": "microsoft",
                },
            ],
        )
        replace_github_category_repos(
            session,
            "cybersecurity",
            [
                {
                    "full_name": "owasp/cheat-sheets",
                    "html_url": "https://github.com/owasp/cheat-sheets",
                    "stars": 800,
                    "forks": 50,
                    "language": "Python",
                }
            ],
        )

    client = login_admin(TestClient(app))
    all_page = client.get("/projects")
    assert all_page.status_code == 200
    assert "Top projects" in all_page.text
    assert "ML-For-Beginners" in all_page.text
    assert "microsoft/ML-For-Beginners" in all_page.text
    assert "Details" in all_page.text
    assert "/static/projects.js" in all_page.text
    assert "huggingface/transformers" not in all_page.text
    assert "owasp/cheat-sheets" in all_page.text
    assert 'href="/projects">All</a>' in all_page.text
    assert "proj-boards" not in all_page.text
    assert "proj-list" in all_page.text
    assert "Not cached yet" not in all_page.text
    assert all_page.text.index("ML-For-Beginners") < all_page.text.index("owasp/cheat-sheets")
    assert 'href="/reports"' not in all_page.text
    assert 'href="/projects"' in all_page.text

    filtered = client.get("/projects?category=machine-learning")
    assert filtered.status_code == 200
    assert "ML-For-Beginners" in filtered.text
    assert "Details" in filtered.text
    assert "huggingface/transformers" not in filtered.text
    assert "owasp/cheat-sheets" not in filtered.text
    assert "01" in filtered.text
    assert "12 weeks of classic Machine Learning" in filtered.text

    missing = client.get("/projects?category=quantum-computing")
    assert missing.status_code == 200
    assert "No repositories in this category yet" in missing.text
    assert "huggingface/transformers" not in missing.text


def test_library_nav_has_top_projects_not_reports(tmp_db):
    client = login_admin(TestClient(app))
    page = client.get("/library")
    assert page.status_code == 200
    assert 'href="/projects"' in page.text
    assert "Top projects" in page.text
    assert 'href="/reports"' not in page.text
