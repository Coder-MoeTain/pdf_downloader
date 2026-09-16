from __future__ import annotations

import zipfile
from io import BytesIO

from sqlalchemy import select

from app.database.connection import session_scope
from app.database.models import Paper, SavedSearch, User
from app.database.repository import related_papers_with_reasons, save_search_config
from app.database.research_models import ExtractionField, ProjectPaper
from app.models.paper import PaperRecord
from app.services.alert_service import alert_is_due, apply_alert_results
from app.services.catalog_service import refresh_catalogs, upsert_reproducibility
from app.services.citation_graph import citation_graph
from app.services.extraction_service import project_schema, set_extracted_value
from app.services.prisma_service import prisma_pdf, prisma_png
from app.services.project_service import add_papers_to_project, create_project
from app.services.report_builder import docx_report, html_report, latex_report, snapshot_payload
from app.services.zotero_service import is_configured, items_from_papers


def _paper(session, title: str, **kwargs) -> Paper:
    paper = Paper(
        title=title,
        normalized_title=title.lower(),
        status="OA_AVAILABLE",
        publication_year=kwargs.get("year", 2024),
        keywords=kwargs.get("keywords", "intrusion detection"),
        journal=kwargs.get("journal", "Computers & Security"),
        doi=kwargs.get("doi"),
    )
    session.add(paper)
    session.flush()
    return paper


def test_prisma_png_and_pdf_bytes():
    counts = {
        "identified": 12,
        "duplicates_removed": 2,
        "records_screened": 10,
        "records_excluded": 4,
        "reports_sought": 6,
        "reports_not_retrieved": 1,
        "reports_assessed": 5,
        "reports_excluded": 2,
        "studies_included": 3,
    }
    png = prisma_png(counts, title="Flow")
    pdf = prisma_pdf(counts, title="Flow")
    assert png.startswith(b"\x89PNG")
    assert pdf.startswith(b"%PDF")


def test_catalogs_graph_reports_and_alerts(auth_client, tmp_db):
    with session_scope() as session:
        user = session.scalar(select(User).limit(1))
        project = create_project(session, user.id, title="Catalog Study", review_type="systematic_review")
        paper = _paper(session, "CICIDS2017 CNN detector", keywords="intrusion detection; cicids2017")
        other = _paper(session, "NSL-KDD random forest", keywords="intrusion detection; nsl-kdd", journal="IEEE Access")
        add_papers_to_project(session, project, user.id, [paper.id, other.id])
        item = session.scalar(select(ProjectPaper).where(ProjectPaper.project_id == project.id, ProjectPaper.paper_id == paper.id))
        schema = project_schema(session, project.id)
        dataset_field = session.scalar(
            select(ExtractionField).where(ExtractionField.schema_id == schema.id, ExtractionField.key == "datasets")
        )
        algo_field = session.scalar(
            select(ExtractionField).where(ExtractionField.schema_id == schema.id, ExtractionField.key == "algorithms")
        )
        set_extracted_value(session, project, user.id, item.id, dataset_field.id, "CICIDS2017", state="user_edited")
        set_extracted_value(session, project, user.id, item.id, algo_field.id, "CNN", state="user_edited")
        catalogs = refresh_catalogs(session, project)
        assert any(row.name == "CICIDS2017" for row in catalogs["datasets"])
        assert any(row.name == "CNN" for row in catalogs["algorithms"])
        upsert_reproducibility(session, project, item.id, code_available="yes", code_url="https://example.org/code")
        graph = citation_graph(session, project, included_only=False)
        assert len(graph["nodes"]) >= 2
        assert graph["disclaimer"]
        related = related_papers_with_reasons(session, paper.id)
        assert related
        assert related[0]["reasons"]
        payload = snapshot_payload(session, project)
        html = html_report(payload)
        tex = latex_report(payload)
        docx = docx_report(payload)
        assert "<h1>" in html
        assert r"\begin{document}" in tex
        with zipfile.ZipFile(BytesIO(docx)) as archive:
            assert "word/document.xml" in archive.namelist()
        saved = save_search_config(session, user.id, "IDS watch", "intrusion detection", {}, alert_enabled=True)
        first = apply_alert_results(session, saved, [PaperRecord(title="New IDS paper", doi="10.1000/alert-1")])
        assert first["new_count"] == 1
        assert first["downloaded"] is False
        session.refresh(saved)
        second = apply_alert_results(session, saved, [PaperRecord(title="New IDS paper", doi="10.1000/alert-1")])
        assert second["new_count"] == 0
        assert alert_is_due(saved) is False
        assert is_configured() is False
        assert items_from_papers([paper])[0]["title"]
        ids = (project.id, paper.id)

    catalogs_page = auth_client.get(f"/projects/{ids[0]}/catalogs")
    assert catalogs_page.status_code == 200
    assert "CICIDS2017" in catalogs_page.text
    graph_page = auth_client.get(f"/projects/{ids[0]}/graph")
    assert graph_page.status_code == 200
    graph_json = auth_client.get(f"/projects/{ids[0]}/graph.json")
    assert graph_json.status_code == 200
    assert "nodes" in graph_json.json()
    png = auth_client.get(f"/projects/{ids[0]}/prisma.png")
    assert png.status_code == 200
    assert png.content.startswith(b"\x89PNG")
    html_page = auth_client.get(f"/projects/{ids[0]}/report.html")
    assert html_page.status_code == 200
    tex_page = auth_client.get(f"/projects/{ids[0]}/report.tex")
    assert tex_page.status_code == 200
    docx_page = auth_client.get(f"/projects/{ids[0]}/report.docx")
    assert docx_page.status_code == 200
    xlsx = auth_client.get(f"/projects/{ids[0]}/matrix.xlsx")
    assert xlsx.status_code == 200
    zotero = auth_client.get(f"/projects/{ids[0]}/zotero.json")
    assert zotero.status_code == 200
    push = auth_client.post(f"/projects/{ids[0]}/zotero/push", follow_redirects=True)
    assert push.status_code == 200
    assert "Zotero" in push.text or "optional" in push.text.lower() or "configure" in push.text.lower()
    discover = auth_client.get("/discover")
    assert discover.status_code == 200
    assert "Alert" in discover.text or "alert" in discover.text.lower()
    reader = auth_client.get(f"/projects/{ids[0]}/papers/{ids[1]}/reader")
    assert reader.status_code == 200
    assert "Related papers" in reader.text


def test_saved_search_alert_form(auth_client):
    saved = auth_client.post(
        "/search/save",
        data={"query": "satellite cybersecurity", "name": "Sat watch", "alert_enabled": "1", "alert_frequency": "daily"},
        follow_redirects=True,
    )
    assert saved.status_code == 200
    with session_scope() as session:
        row = session.scalar(select(SavedSearch).order_by(SavedSearch.id.desc()))
        assert row is not None
        assert row.alert_enabled is True
        assert row.alert_frequency == "daily"
        search_id = row.id
    page = auth_client.post(f"/search/saved/{search_id}/alert", data={"alert_frequency": "weekly"}, follow_redirects=True)
    assert page.status_code == 200
    with session_scope() as session:
        row = session.get(SavedSearch, search_id)
        assert row.alert_enabled is False
        assert row.alert_frequency == "weekly"
