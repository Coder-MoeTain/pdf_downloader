from fastapi.testclient import TestClient

from app.database.connection import session_scope
from app.database.models import Paper
from app.database.repository import apply_paper_filters, save_paper, set_paper_rating
from app.models.paper import PaperRecord, PaperStatus
from app.web import app
from sqlalchemy import select


def test_user_rating_set_and_clear(tmp_db):
    record = PaperRecord(title="Rated paper", doi="10.1000/rate-me", status=PaperStatus.FOUND)
    with session_scope() as session:
        paper = save_paper(session, record)
        paper_id = paper.id
        set_paper_rating(session, paper_id, 5)
    with session_scope() as session:
        paper = session.get(Paper, paper_id)
        assert paper.user_rating == 5
        set_paper_rating(session, paper_id, 0)
    with session_scope() as session:
        paper = session.get(Paper, paper_id)
        assert paper.user_rating is None


def test_user_rating_survives_metadata_update(tmp_db):
    record = PaperRecord(title="Keep stars", doi="10.1000/keep-stars", status=PaperStatus.FOUND)
    with session_scope() as session:
        paper = save_paper(session, record)
        set_paper_rating(session, paper.id, 4)
        paper_id = paper.id
    updated = PaperRecord(title="Keep stars", doi="10.1000/keep-stars", status=PaperStatus.OA_AVAILABLE)
    with session_scope() as session:
        save_paper(session, updated)
        paper = session.get(Paper, paper_id)
        assert paper.user_rating == 4
        assert paper.status == "OA_AVAILABLE"


def test_downloadable_filter_excludes_paywalled(tmp_db):
    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(
                title="Open PDF",
                doi="10.1000/oa",
                pdf_url="https://arxiv.org/pdf/1234.5678.pdf",
                status=PaperStatus.OA_AVAILABLE,
            ),
        )
        save_paper(
            session,
            PaperRecord(title="Closed", doi="10.1000/closed", status=PaperStatus.PAYWALLED),
        )
        save_paper(
            session,
            PaperRecord(title="No file", doi="10.1000/none", status=PaperStatus.NO_PDF),
        )
        stmt = apply_paper_filters(select(Paper), downloadable=True)
        titles = {p.title for p in session.scalars(stmt)}
        assert titles == {"Open PDF"}


def test_min_rating_filter(tmp_db):
    with session_scope() as session:
        low = save_paper(session, PaperRecord(title="Low", doi="10.1000/low", status=PaperStatus.FOUND))
        high = save_paper(session, PaperRecord(title="High", doi="10.1000/high", status=PaperStatus.FOUND))
        set_paper_rating(session, low.id, 2)
        set_paper_rating(session, high.id, 5)
        titles = {p.title for p in session.scalars(apply_paper_filters(select(Paper), min_rating=4))}
        assert titles == {"High"}


def test_rating_api_and_downloadable_page(tmp_db):
    with session_scope() as session:
        paper = save_paper(
            session,
            PaperRecord(
                title="OA paper for dashboard",
                doi="10.1000/api-rate",
                pdf_url="https://arxiv.org/pdf/1.pdf",
                status=PaperStatus.OA_AVAILABLE,
            ),
        )
        paper_id = paper.id
    client = TestClient(app)
    response = client.post(f"/api/papers/{paper_id}/rating", json={"rating": 5})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "rating": 5}
    page = client.get("/library?pdf=1")
    assert page.status_code == 200
    assert "OA paper for dashboard" in page.text
    assert "downloadable pdf" in page.text.lower()
    rated = client.get("/library?min_rating=4")
    assert rated.status_code == 200
    assert "OA paper for dashboard" in rated.text
    empty = client.get("/library?min_rating=5&status=PAYWALLED")
    assert empty.status_code == 200
    assert "Nothing matches this filter" in empty.text


def test_category_year_source_and_journal_filters(tmp_db):
    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(
                title="Satellite cyber paper",
                doi="10.1000/sat-cat",
                publication_year=2021,
                journal="Sensors",
                source_provider="arxiv",
                research_fields=["Computer Science", "Cybersecurity"],
                keywords=["satellites"],
                status=PaperStatus.OA_AVAILABLE,
            ),
        )
        save_paper(
            session,
            PaperRecord(
                title="ML vision paper",
                doi="10.1000/ml-cat",
                publication_year=2019,
                journal="Nature Machine Intelligence",
                source_provider="openalex",
                research_fields=["Computer Science", "Machine Learning"],
                status=PaperStatus.FOUND,
            ),
        )
        cyber = {p.title for p in session.scalars(apply_paper_filters(select(Paper), category="Cybersecurity"))}
        assert cyber == {"Satellite cyber paper"}
        year_2019 = {p.title for p in session.scalars(apply_paper_filters(select(Paper), year=2019))}
        assert year_2019 == {"ML vision paper"}
        arxiv = {p.title for p in session.scalars(apply_paper_filters(select(Paper), source="arxiv"))}
        assert arxiv == {"Satellite cyber paper"}
        sensors = {p.title for p in session.scalars(apply_paper_filters(select(Paper), journal="Sensors"))}
        assert sensors == {"Satellite cyber paper"}
    client = TestClient(app)
    page = client.get("/library?category=Computer%20Science")
    assert page.status_code == 200
    assert "Satellite cyber paper" in page.text
    assert "ML vision paper" in page.text
    assert "Categories" in page.text
    assert "Research topics" in page.text
    assert "lib-topic" in page.text
    year_page = client.get("/library?year=2021")
    assert "Satellite cyber paper" in year_page.text
    assert "ML vision paper" not in year_page.text
    source_page = client.get("/library?source=openalex")
    assert "ML vision paper" in source_page.text
    assert "Satellite cyber paper" not in source_page.text


def test_library_pagination_controls(tmp_db):
    with session_scope() as session:
        for index in range(12):
            save_paper(
                session,
                PaperRecord(
                    title=f"Paged paper {index:02d}",
                    doi=f"10.1000/page-{index}",
                    status=PaperStatus.FOUND,
                    relevance_score=100 - index,
                ),
            )
    client = TestClient(app)
    first = client.get("/library?per_page=10")
    assert first.status_code == 200
    assert "Showing" in first.text
    assert "1–10" in first.text
    assert "of <strong>12</strong>" in first.text or "of 12" in first.text
    assert "Paged paper 00" in first.text
    assert "Paged paper 11" not in first.text
    assert 'aria-label="Library pages"' in first.text
    second = client.get("/library?per_page=10&page=2")
    assert second.status_code == 200
    assert "Paged paper 11" in second.text
    assert "Paged paper 00" not in second.text
    assert "Page 2 of 2" in second.text


def test_downloads_pagination_and_search(tmp_db):
    from app.database.repository import upsert_download

    with session_scope() as session:
        for index in range(12):
            paper = save_paper(
                session,
                PaperRecord(
                    title=f"Download row {index:02d}",
                    doi=f"10.1000/dl-{index}",
                    pdf_url="https://arxiv.org/pdf/1.pdf",
                    status=PaperStatus.DOWNLOADED,
                ),
            )
            upsert_download(
                session,
                paper.id,
                pdf_url="https://arxiv.org/pdf/1.pdf",
                status=PaperStatus.DOWNLOADED.value,
                local_path=f"library/{index}.pdf",
                file_size=2048,
            )
        match = save_paper(
            session,
            PaperRecord(
                title="Satellite drought paper",
                doi="10.1000/dl-search",
                pdf_url="https://arxiv.org/pdf/2.pdf",
                status=PaperStatus.DOWNLOADED,
            ),
        )
        upsert_download(
            session,
            match.id,
            pdf_url="https://arxiv.org/pdf/2.pdf",
            status=PaperStatus.DOWNLOADED.value,
            local_path="library/search.pdf",
            file_size=4096,
        )
    client = TestClient(app)
    first = client.get("/downloads?per_page=10")
    assert first.status_code == 200
    assert "Showing" in first.text
    assert "1–10" in first.text
    assert "of <strong>13</strong>" in first.text or "of 13" in first.text
    assert 'aria-label="Download pages"' in first.text
    assert "Per page" in first.text
    assert "Download row 00" not in first.text
    second = client.get("/downloads?per_page=10&page=2")
    assert second.status_code == 200
    assert "Page 2 of 2" in second.text
    assert "Download row 00" in second.text
    searched = client.get("/downloads?q=Satellite")
    assert searched.status_code == 200
    assert "Satellite drought paper" in searched.text
    assert "Download row 00" not in searched.text
    assert "Nothing matches this filter" not in searched.text
    assert 'id="downloadLogs"' in first.text
    assert "Saved" in first.text or "Waiting for a download" in first.text


def test_downloads_page_marks_recent_pdfs_as_new(tmp_db):
    from datetime import timedelta
    from types import SimpleNamespace

    from app.database.repository import upsert_download
    from app.utils.time import utc_now
    from app.web.ui import is_new_download

    now = utc_now()
    assert is_new_download(
        SimpleNamespace(status="DOWNLOADED", local_path="library/a.pdf", downloaded_at=now),
        now=now,
    )
    assert not is_new_download(
        SimpleNamespace(
            status="DOWNLOADED",
            local_path="library/a.pdf",
            downloaded_at=now - timedelta(hours=25),
        ),
        now=now,
    )
    assert not is_new_download(
        SimpleNamespace(status="FAILED", local_path="library/a.pdf", downloaded_at=now),
        now=now,
    )

    with session_scope() as session:
        fresh = save_paper(
            session,
            PaperRecord(
                title="Freshly saved paper",
                doi="10.1000/dl-new",
                pdf_url="https://arxiv.org/pdf/new.pdf",
                status=PaperStatus.DOWNLOADED,
            ),
        )
        upsert_download(
            session,
            fresh.id,
            pdf_url="https://arxiv.org/pdf/new.pdf",
            status=PaperStatus.DOWNLOADED.value,
            local_path="library/new.pdf",
            file_size=2048,
        )
        aged = save_paper(
            session,
            PaperRecord(
                title="Older saved paper",
                doi="10.1000/dl-old",
                pdf_url="https://arxiv.org/pdf/old.pdf",
                status=PaperStatus.DOWNLOADED,
            ),
        )
        old_row = upsert_download(
            session,
            aged.id,
            pdf_url="https://arxiv.org/pdf/old.pdf",
            status=PaperStatus.DOWNLOADED.value,
            local_path="library/old.pdf",
            file_size=2048,
        )
        old_row.downloaded_at = utc_now() - timedelta(days=3)

    client = TestClient(app)
    page = client.get("/downloads")
    assert page.status_code == 200
    assert "Freshly saved paper" in page.text
    assert "Older saved paper" in page.text
    assert page.text.count('class="label-new"') == 1
    assert page.text.count('class="is-new"') == 1
    assert "label-new" in page.text.split("Freshly saved paper", 1)[1].split("</td>", 1)[0]
    assert "label-new" not in page.text.split("Older saved paper", 1)[1].split("</td>", 1)[0]


def test_library_hides_no_pdf_and_failed(tmp_db):
    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(
                title="Open visible paper",
                doi="10.1000/open-ok",
                pdf_url="https://arxiv.org/pdf/1.pdf",
                status=PaperStatus.OA_AVAILABLE,
            ),
        )
        save_paper(session, PaperRecord(title="No file paper", doi="10.1000/no-pdf", status=PaperStatus.NO_PDF))
        save_paper(
            session,
            PaperRecord(title="Broken download paper", doi="10.1000/failed", status=PaperStatus.FAILED),
        )
        save_paper(session, PaperRecord(title="Skipped paper", doi="10.1000/skipped", status=PaperStatus.SKIPPED))
    client = TestClient(app)
    library = client.get("/library")
    assert library.status_code == 200
    assert "Open visible paper" in library.text
    assert "No file paper" not in library.text
    assert "Broken download paper" not in library.text
    assert "Skipped paper" not in library.text
    assert "No legal PDF" not in library.text
    assert ">Failed<" not in library.text
    assert 'value="NO_PDF"' not in library.text
    assert 'value="FAILED"' not in library.text
    explicit = client.get("/library?status=FAILED")
    assert explicit.status_code == 200
    assert "Broken download paper" in explicit.text
    assert "Failed" in explicit.text


def test_library_status_panel_shows_counts(tmp_db):
    from app.database.repository import library_facets
    from app.web.ui import library_status_panel

    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(
                title="Open visible paper",
                doi="10.1000/status-oa",
                pdf_url="https://arxiv.org/pdf/1.pdf",
                status=PaperStatus.OA_AVAILABLE,
            ),
        )
        save_paper(
            session,
            PaperRecord(
                title="Saved PDF paper",
                doi="10.1000/status-dl",
                pdf_url="https://arxiv.org/pdf/2.pdf",
                status=PaperStatus.DOWNLOADED,
            ),
        )
        save_paper(session, PaperRecord(title="Found paper", doi="10.1000/status-found", status=PaperStatus.FOUND))
        save_paper(
            session,
            PaperRecord(title="Closed paywalled paper", doi="10.1000/status-pay", status=PaperStatus.PAYWALLED),
        )
        save_paper(session, PaperRecord(title="Broken download paper", doi="10.1000/status-fail", status=PaperStatus.FAILED))
        stats = library_status_panel(library_facets(session))
    assert stats["visible_total"] == 4
    assert stats["downloadable"] == 2
    assert stats["downloaded"] == 1
    assert stats["open_access"] == 1
    assert stats["paywalled"] == 1
    assert stats["has_stats"] is True
    labels = {row["label"]: row["count"] for row in stats["statuses"]}
    assert labels["Open access"] == 1
    assert labels["Downloaded"] == 1
    assert labels["Found"] == 1
    assert labels["Paywalled"] == 1
    assert "Failed" not in labels

    client = TestClient(app)
    page = client.get("/library")
    assert page.status_code == 200
    assert 'class="lib-status-panel"' in page.text
    assert 'aria-label="Library statistics"' in page.text
    assert "Downloadable PDFs" in page.text
    assert "Access mix" in page.text
    assert "Open access · 1" in page.text
    assert "Downloaded · 1" in page.text
    assert "Found · 1" in page.text
    assert "Paywalled · 1" in page.text
    assert 'href="/library?status=DOWNLOADED"' in page.text
    filtered = client.get("/library?status=DOWNLOADED")
    assert filtered.status_code == 200
    assert "lib-kpi is-active" in filtered.text
    assert "Saved PDF paper" in filtered.text
    assert "Open visible paper" not in filtered.text


def test_hide_paywalled_filters_library_unless_explicit(tmp_db):
    from app.database.repository import upsert_download
    from app.database.settings_repository import save_workspace_settings

    with session_scope() as session:
        open_paper = save_paper(
            session,
            PaperRecord(title="Open visible paper", doi="10.1000/open-visible", status=PaperStatus.OA_AVAILABLE),
        )
        closed = save_paper(
            session,
            PaperRecord(title="Closed paywalled paper", doi="10.1000/closed-hidden", status=PaperStatus.PAYWALLED),
        )
        upsert_download(session, open_paper.id, pdf_url="https://arxiv.org/pdf/1.pdf", status=PaperStatus.OA_AVAILABLE.value)
        upsert_download(session, closed.id, pdf_url=None, status=PaperStatus.PAYWALLED.value)
    save_workspace_settings(
        {
            "contact_email": "lab@university.edu",
            "library_dir": "research_library",
            "timezone": "UTC",
            "check_robots_txt": True,
            "prefer_https": True,
            "show_paywalled": False,
        }
    )
    client = TestClient(app)
    library = client.get("/library")
    assert library.status_code == 200
    assert "Open visible paper" in library.text
    assert "Closed paywalled paper" not in library.text
    explicit = client.get("/library?status=PAYWALLED")
    assert explicit.status_code == 200
    assert "Closed paywalled paper" in explicit.text
    downloads = client.get("/downloads")
    assert downloads.status_code == 200
    assert "Open visible paper" in downloads.text
    assert "Closed paywalled paper" not in downloads.text
    assert "/downloads?status=PAYWALLED" not in downloads.text
    paywalled_downloads = client.get("/downloads?status=PAYWALLED")
    assert paywalled_downloads.status_code == 200
    assert "Closed paywalled paper" in paywalled_downloads.text
    home = client.get("/")
    assert home.status_code == 200
    assert "sources ready" not in home.text
    assert "Peak year" not in home.text
    assert "insight-chip" not in home.text
    assert "store-pill" not in home.text
    assert "Analytics" in home.text
    settings = client.get("/settings?section=workspace")
    assert settings.status_code == 200
    assert "Show paywalled papers" in settings.text
    assert 'name="show_paywalled"' in settings.text


def test_library_abstract_preview_button(tmp_db):
    from app.models.paper import AuthorRecord

    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(
                title="Paper with abstract",
                doi="10.1000/abs-preview",
                abstract="Satellites can observe drought from orbit.",
                authors=[AuthorRecord(name="Ada Lovelace")],
                publication_year=2024,
                journal="Remote Sensing",
                pdf_url="https://arxiv.org/pdf/1.pdf",
                status=PaperStatus.OA_AVAILABLE,
            ),
        )
        save_paper(
            session,
            PaperRecord(
                title="Paper without abstract",
                doi="10.1000/no-abs",
                pdf_url="https://arxiv.org/pdf/2.pdf",
                status=PaperStatus.OA_AVAILABLE,
            ),
        )
    client = TestClient(app)
    page = client.get("/library")
    assert page.status_code == 200
    assert 'id="abstractPreviewModal"' in page.text
    assert page.text.count("abstract-btn") == 1
    assert "Satellites can observe drought from orbit." in page.text
    assert "Ada Lovelace" in page.text
    assert "Paper with abstract" in page.text
    assert "Paper without abstract" in page.text


def test_library_shows_who_downloaded(tmp_db):
    from app.auth import create_local_user
    from app.database.repository import upsert_download

    with session_scope() as session:
        user = create_local_user(session, email="alice@lab.edu", password="password1", name="Alice")
        paper = save_paper(
            session,
            PaperRecord(
                title="Downloaded by Alice",
                doi="10.1000/alice-dl",
                pdf_url="https://arxiv.org/pdf/1.pdf",
                status=PaperStatus.DOWNLOADED,
            ),
        )
        upsert_download(
            session,
            paper.id,
            pdf_url="https://arxiv.org/pdf/1.pdf",
            status=PaperStatus.DOWNLOADED.value,
            local_path="library/alice.pdf",
            user_id=user.id,
        )
        save_paper(
            session,
            PaperRecord(
                title="Not downloaded yet",
                doi="10.1000/no-dl",
                pdf_url="https://arxiv.org/pdf/2.pdf",
                status=PaperStatus.OA_AVAILABLE,
            ),
        )
    client = TestClient(app)
    login = client.post(
        "/login",
        data={"email": "alice@lab.edu", "password": "password1", "next": "/library"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    page = client.get("/library")
    assert page.status_code == 200
    assert "Downloaded by" in page.text
    assert "Alice" in page.text
    assert "Downloaded by Alice" in page.text
    assert "Not downloaded yet" in page.text
    assert 'class="lib-col-date">Date</th>' in page.text
    assert "lib-date" in page.text


def test_library_and_downloads_show_record_dates(tmp_db):
    from datetime import datetime
    from types import SimpleNamespace

    from app.database.repository import upsert_download
    from app.utils.time import format_local, utc_now
    from app.web.ui import download_record_date, paper_record_date

    added = datetime(2026, 3, 15, 8, 30, 0)
    saved = datetime(2026, 4, 1, 12, 0, 0)
    assert paper_record_date(SimpleNamespace(created_at=added, downloads=[])) == added
    assert (
        paper_record_date(
            SimpleNamespace(created_at=added, downloads=[SimpleNamespace(id=1, downloaded_at=saved)])
        )
        == saved
    )
    assert download_record_date(SimpleNamespace(downloaded_at=saved, paper=None)) == saved
    assert (
        download_record_date(SimpleNamespace(downloaded_at=None, paper=SimpleNamespace(created_at=added)))
        == added
    )

    with session_scope() as session:
        paper = save_paper(
            session,
            PaperRecord(
                title="Dated library paper",
                doi="10.1000/dated-row",
                pdf_url="https://arxiv.org/pdf/dated.pdf",
                status=PaperStatus.DOWNLOADED,
            ),
        )
        upsert_download(
            session,
            paper.id,
            pdf_url="https://arxiv.org/pdf/dated.pdf",
            status=PaperStatus.DOWNLOADED.value,
            local_path="library/dated.pdf",
            file_size=1024,
        )

    client = TestClient(app)
    library = client.get("/library")
    assert library.status_code == 200
    assert 'class="lib-col-date">Date</th>' in library.text
    assert "Dated library paper" in library.text
    assert "lib-date" in library.text
    downloads = client.get("/downloads")
    assert downloads.status_code == 200
    assert 'class="dl-col-date">Date</th>' in downloads.text
    assert "Dated library paper" in downloads.text
    assert "dl-date" in downloads.text
    today = format_local(utc_now(), "%Y-%m-%d")
    assert today in library.text
    assert today in downloads.text


def test_admin_can_delete_library_paper(tmp_db):
    from app.database.models import SearchResult
    from app.database.repository import attach_search_result, create_search_query, delete_library_paper

    with session_scope() as session:
        paper = save_paper(
            session,
            PaperRecord(title="Remove me", doi="10.1000/delete-me", status=PaperStatus.FOUND),
        )
        paper_id = paper.id
        search = create_search_query(session, "topic", [], {})
        attach_search_result(session, search.id, paper.id, 1, 0.5)
    with session_scope() as session:
        title, paths = delete_library_paper(session, paper_id)
        assert title == "Remove me"
        assert paths == []
    with session_scope() as session:
        assert session.get(Paper, paper_id) is None
        leftover = session.scalars(select(SearchResult).where(SearchResult.paper_id == paper_id)).all()
        assert leftover == []
    client = TestClient(app)
    created = client.post(
        "/login",
        data={"email": "admin@lab.test", "password": "secret123", "name": "Admin", "next": "/"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    with session_scope() as session:
        other = save_paper(
            session,
            PaperRecord(title="Keep listing", doi="10.1000/keep-list", status=PaperStatus.FOUND),
        )
        other_id = other.id
    page = client.get("/library")
    assert page.status_code == 200
    assert "Delete" in page.text
    deleted = client.post(f"/papers/{other_id}/delete", data={"next": "/library"}, follow_redirects=False)
    assert deleted.status_code == 303
    with session_scope() as session:
        assert session.get(Paper, other_id) is None


def test_non_admin_cannot_delete_library_paper(tmp_db, monkeypatch):
    user = {
        "id": 1,
        "email": "reader@gmail.com",
        "name": "Reader",
        "picture": "",
        "role": "user",
        "is_admin": False,
        "has_password": True,
    }
    monkeypatch.setattr("app.web.google_login_enabled", lambda: True)
    monkeypatch.setattr("app.auth.google_login_enabled", lambda: True)
    monkeypatch.setattr("app.web.auth_required", lambda: True)
    monkeypatch.setattr("app.auth.auth_required", lambda: True)
    monkeypatch.setattr("app.web.current_user", lambda _request: user)
    monkeypatch.setattr("app.auth.current_user", lambda _request: user)
    monkeypatch.setattr("app.web.user_is_admin", lambda _request: False)
    monkeypatch.setattr("app.auth.user_is_admin", lambda _request: False)
    monkeypatch.setattr("app.web.user_role", lambda _value: "user")
    with session_scope() as session:
        paper = save_paper(
            session,
            PaperRecord(title="Stay put", doi="10.1000/stay", status=PaperStatus.FOUND),
        )
        paper_id = paper.id
    client = TestClient(app, follow_redirects=False)
    page = client.get("/library")
    assert page.status_code == 200
    assert ">Delete<" not in page.text
    denied = client.post(f"/papers/{paper_id}/delete", data={"next": "/library"})
    assert denied.status_code == 303
    with session_scope() as session:
        assert session.get(Paper, paper_id) is not None
