"""Ensure single-paper PDF downloads do not freeze the web event loop."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD


@pytest.mark.asyncio
async def test_paper_pdf_route_stays_responsive_while_fetching(tmp_db, tmp_path, monkeypatch):
    from app.web import app

    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    async def slow_ensure(paper_id: int, topic_slug: str = "library", user_id=None) -> Path:
        await asyncio.sleep(0.6)
        return pdf

    monkeypatch.setattr("app.web.routes.downloads.ensure_local_pdf", slow_ensure)
    monkeypatch.setattr("app.web.ensure_local_pdf", slow_ensure)
    monkeypatch.setattr("app.web.routes.downloads.record_usage", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.web.record_usage", lambda *args, **kwargs: None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        login_page = await client.get("/login")
        assert login_page.status_code == 200
        token = client.cookies.get("csrf_token")
        signed = await client.post(
            "/login",
            data={
                "email": TEST_ADMIN_EMAIL,
                "password": TEST_ADMIN_PASSWORD,
                "csrf_token": token,
                "next": "/",
            },
            headers={"X-CSRF-Token": token or ""},
            follow_redirects=False,
        )
        assert signed.status_code in {200, 302, 303}
        download_task = asyncio.create_task(client.get("/papers/1/pdf"))
        await asyncio.sleep(0.05)
        t0 = time.perf_counter()
        progress = await client.get("/api/download-progress")
        elapsed = time.perf_counter() - t0
        assert progress.status_code == 200
        # If ensure_local_pdf ran on the uvicorn loop, this would wait ~0.6s+.
        assert elapsed < 0.35, f"event loop blocked for {elapsed:.2f}s during PDF fetch"
        response = await download_task
        assert response.status_code == 200
        assert response.headers.get("content-type", "").startswith("application/pdf")
