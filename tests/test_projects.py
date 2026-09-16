from __future__ import annotations

from app.auth import create_local_user
from app.database.connection import session_scope
from app.database.models import Paper
from app.database.research_models import ExtractionField, ProjectPaper
from app.services.extraction_service import matrix_rows, project_schema, set_extracted_value
from app.services.prisma_service import prisma_counts
from app.services.project_service import add_papers_to_project, create_project
from app.services.research_rag import answer_from_evidence
from app.services.screening_service import record_decision
from tests.conftest import CsrfAwareTestClient


def _paper(session, title: str = "Web vulnerability detection with ML") -> Paper:
    paper = Paper(title=title, normalized_title=title.lower(), status="OA_AVAILABLE", publication_year=2024)
    session.add(paper)
    session.flush()
    return paper


def test_create_project_and_questions(auth_client):
    page = auth_client.get("/projects")
    assert page.status_code == 200
    assert "Projects" in page.text
    created = auth_client.post(
        "/projects",
        data={"title": "Satellite Cybersecurity", "review_type": "systematic_review", "description": "SLR"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    location = created.headers["location"]
    overview = auth_client.get(location)
    assert overview.status_code == 200
    assert "Satellite Cybersecurity" in overview.text
    assert "Systematic" in overview.text or "systematic" in overview.text.lower()
    project_id = int(location.rstrip("/").split("/")[-1])
    auth_client.post(f"/projects/{project_id}/questions", data={"question": "Which datasets are used?", "code": "RQ1"})
    again = auth_client.get(f"/projects/{project_id}")
    assert "Which datasets are used?" in again.text


def test_project_idor(auth_client, tmp_db):
    created = auth_client.post("/projects", data={"title": "Owner Project Alpha"}, follow_redirects=False)
    project_id = int(created.headers["location"].rstrip("/").split("/")[-1])
    from app.auth import invalidate_user_count_cache

    with session_scope() as session:
        create_local_user(session, email="reviewer@test.local", password="Reviewer1!", name="Reviewer", role="user")
    invalidate_user_count_cache()
    other = CsrfAwareTestClient(__import__("app.web", fromlist=["app"]).app)
    other.get("/login")
    other.post("/login", data={"email": "reviewer@test.local", "password": "Reviewer1!", "next": "/"})
    denied = other.get(f"/projects/{project_id}")
    assert (
        denied.status_code in {303, 403, 404}
        or "do not have access" in denied.text.lower()
        or denied.status_code == 200
        and "Owner Project Alpha" not in denied.text
    )
    sneaky = other.post(f"/projects/{project_id}/questions", data={"question": "hack"}, follow_redirects=False)
    assert sneaky.status_code in {303, 403, 404}


def test_screening_exclude_requires_reason_and_prisma(auth_client):
    with session_scope() as session:
        from app.database.models import User

        user = session.scalar(__import__("sqlalchemy", fromlist=["select"]).select(User).limit(1))
        project = create_project(session, user.id, title="Screening Study", review_type="systematic_review")
        paper = _paper(session)
        add_papers_to_project(session, project, user.id, [paper.id])
        item = session.scalar(
            __import__("sqlalchemy", fromlist=["select"])
            .select(ProjectPaper)
            .where(ProjectPaper.project_id == project.id)
        )
        ids = (project.id, item.id, user.id)
    page = auth_client.get(f"/projects/{ids[0]}/screening")
    assert page.status_code == 200
    assert "INCLUDE" in page.text.upper() or "Include" in page.text
    missing = auth_client.post(
        f"/projects/{ids[0]}/screening/{ids[1]}",
        data={"stage": "title_abstract", "decision": "exclude", "reason": ""},
        follow_redirects=True,
    )
    assert missing.status_code == 200
    assert "reason" in missing.text.lower()
    with session_scope() as session:
        project = session.get(
            __import__("app.database.research_models", fromlist=["ResearchProject"]).ResearchProject, ids[0]
        )
        record_decision(session, project, ids[1], ids[2], stage="title_abstract", decision="include")
        record_decision(session, project, ids[1], ids[2], stage="full_text", decision="include")
        counts = prisma_counts(session, project)
    assert counts["identified"] >= 1
    assert counts["studies_included"] >= 1
    prisma_page = auth_client.get(f"/projects/{ids[0]}/prisma")
    assert prisma_page.status_code == 200
    assert "studies included" in prisma_page.text.lower() or "Included" in prisma_page.text


def test_extraction_evidence_and_matrix(auth_client):
    with session_scope() as session:
        from sqlalchemy import select

        from app.database.models import User

        user = session.scalar(select(User).limit(1))
        project = create_project(session, user.id, title="Extraction Study")
        paper = _paper(session, "CICIDS2017 evaluation")
        add_papers_to_project(session, project, user.id, [paper.id])
        item = session.scalar(select(ProjectPaper).where(ProjectPaper.project_id == project.id))
        schema = project_schema(session, project.id)
        field = session.scalar(
            select(ExtractionField).where(ExtractionField.schema_id == schema.id, ExtractionField.key == "f1_score")
        )
        set_extracted_value(
            session,
            project,
            user.id,
            item.id,
            field.id,
            "0.94",
            method="manual",
            state="user_edited",
            evidence_text="F1 reached 0.94 on CICIDS2017.",
            page_number=8,
            section="Results",
        )
        payload = matrix_rows(session, project)
        assert payload["rows"][0]["cells"]
        f1 = next(cell for cell in payload["rows"][0]["cells"] if cell["key"] == "f1_score")
        assert f1["value"] == "0.94"
        assert f1["page"] == 8
        project_id = project.id
    page = auth_client.get(f"/projects/{project_id}/matrix")
    assert page.status_code == 200
    assert "0.94" in page.text


def test_ask_without_chunks_does_not_fabricate():
    result = answer_from_evidence("What dataset is used?", [])
    assert result["supported"] is False
    assert "could not find sufficient evidence" in result["answer"].lower()


def test_dual_screening_records_conflict(auth_client, tmp_db):
    from sqlalchemy import select

    from app.auth import invalidate_user_count_cache
    from app.database.models import User
    from app.services.project_service import add_member

    with session_scope() as session:
        owner = session.scalar(select(User).limit(1))
        reviewer = create_local_user(session, email="dual@test.local", password="Reviewer1!", name="Dual", role="user")
        invalidate_user_count_cache()
        project = create_project(session, owner.id, title="Dual Review Study", review_type="systematic_review")
        project.dual_screening = True
        paper = _paper(session, "Dual screening paper")
        add_papers_to_project(session, project, owner.id, [paper.id])
        add_member(session, project, owner.id, reviewer.id, "reviewer")
        item = session.scalar(select(ProjectPaper).where(ProjectPaper.project_id == project.id))
        first = record_decision(session, project, item.id, owner.id, stage="title_abstract", decision="include")
        assert first["conflict"] is False
        session.refresh(item)
        assert item.decision == "pending"
        second = record_decision(
            session,
            project,
            item.id,
            reviewer.id,
            stage="title_abstract",
            decision="exclude",
            reason="wrong_topic",
        )
        assert second["conflict"] is True
        session.refresh(item)
        assert item.decision == "pending"


def test_discover_alias(auth_client):
    page = auth_client.get("/discover")
    assert page.status_code == 200
    assert "Discover" in page.text
    assert "Projects" in page.text
