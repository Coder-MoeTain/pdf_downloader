#!/usr/bin/env python3
"""Capture dashboard screenshot for README."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images" / "dashboard.png"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def seed_admin() -> tuple[str, str]:
    os.environ.setdefault("DATABASE_PATH", str(ROOT / "data" / "research.db"))
    sys.path.insert(0, str(ROOT))
    from app.database.connection import init_db, session_scope
    from app.auth import create_local_user, authenticate_local
    from app.database.models import User
    from sqlalchemy import select, func

    init_db()
    email = "screenshot@test.local"
    password = "Screenshot1!"
    with session_scope() as session:
        count = session.scalar(select(func.count(User.id))) or 0
        if count == 0:
            create_local_user(session, email=email, password=password, name="Screenshot", role="admin")
        elif authenticate_local(session, email, password) is None:
            create_local_user(session, email=email, password=password, name="Screenshot", role="admin")
    return email, password


def main() -> None:
    email, password = seed_admin()
    env = os.environ.copy()
    env.setdefault("DATABASE_PATH", str(ROOT / "data" / "research.db"))
    proc = subprocess.Popen(
        [str(ROOT / ".venv" / "bin" / "uvicorn"), "app.web:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(2.5)
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1200})
            page.goto(f"{BASE}/login", wait_until="networkidle")
            page.fill('input[name="email"]', email)
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]')
            page.wait_for_url(f"{BASE}/**", timeout=15000)
            page.goto(f"{BASE}/", wait_until="networkidle")
            page.wait_for_timeout(800)
            OUT.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(OUT), full_page=True)
            browser.close()
        print(f"Saved {OUT}")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
