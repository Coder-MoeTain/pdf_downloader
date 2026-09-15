"""Tests for WikiCFP parsing, deadline window, and /cfp page."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.database.connection import session_scope
from app.database.repository import list_upcoming_cfps, upsert_cfp_call
from app.services.cfp_service import (
    cover_image_url_for,
    display_image_url,
    external_id_from_url,
    extract_deadline_from_html,
    extract_og_image,
    extract_website_from_html,
    is_generic_cfp_image,
    parse_cfp_date,
    parse_rss_items,
    strip_html,
)
from app.web import app

SAMPLE_RSS = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>CFPs</title>
    <item>
      <title>ICML 2026</title>
      <link>http://www.wikicfp.com/cfp/servlet/event.showcfp?eventid=12345</link>
      <description>&lt;b&gt;Machine learning conference&lt;/b&gt;</description>
      <pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Missing link</title>
      <description>skip</description>
    </item>
  </channel>
</rss>
"""


def test_parse_rss_items():
    items = parse_rss_items(SAMPLE_RSS)
    assert len(items) == 1
    assert items[0]["title"] == "ICML 2026"
    assert "eventid=12345" in items[0]["link"]
    assert "Machine learning" in strip_html(items[0]["description"])


def test_parse_cfp_date_variants():
    assert parse_cfp_date("Sep 15, 2026") == datetime(2026, 9, 15)
    assert parse_cfp_date("15 September 2026") == datetime(2026, 9, 15)
    assert parse_cfp_date("2026-09-15") == datetime(2026, 9, 15)
    assert parse_cfp_date("TBD") is None
    assert parse_cfp_date("") is None


def test_extract_deadline_and_og_image():
    html = """
    <html><head>
      <meta property="og:image" content="https://example.com/poster.png">
    </head><body>
      Submission Deadline: Oct 20, 2026
      Where: Tokyo, Japan
      Link: <a href="https://conf.example.org/2026" target="_newtab">https://conf.example.org/2026</a>
    </body></html>
    """
    assert extract_og_image(html) == "https://example.com/poster.png"
    assert extract_deadline_from_html(html) == datetime(2026, 10, 20)
    assert extract_website_from_html(html) == "https://conf.example.org/2026"
    assert extract_website_from_html('Link: <a href="http://www.wikicfp.com/x">x</a>') is None


def test_external_id_from_url():
    assert external_id_from_url("http://www.wikicfp.com/cfp/servlet/event.showcfp?eventid=99") == "wikicfp:99"


def test_cover_image_for_generic_favicons():
    assert is_generic_cfp_image("https://www.google.com/s2/favicons?domain=www.wikicfp.com&sz=128")
    assert is_generic_cfp_image("/static/favicon.svg")
    assert not is_generic_cfp_image("https://example.com/poster.png")
    a = cover_image_url_for("wikicfp:1", "cybersecurity")
    b = cover_image_url_for("wikicfp:1", "cybersecurity")
    c = cover_image_url_for("wikicfp:2", "cybersecurity")
    assert a == b
    assert a.startswith("https://images.unsplash.com/")
    assert a != c or cover_image_url_for("wikicfp:3", "satellite") != a
    assert display_image_url(
        "https://www.google.com/s2/favicons?domain=www.wikicfp.com&sz=128",
        external_id="wikicfp:9",
        categories="satellite",
    ).startswith("https://images.unsplash.com/")
    assert (
        display_image_url("https://cdn.example/poster.jpg", external_id="wikicfp:9") == "https://cdn.example/poster.jpg"
    )


def test_list_upcoming_cfps_window(tmp_db):
    now = datetime.utcnow()
    with session_scope() as session:
        upsert_cfp_call(
            session,
            {
                "external_id": "wikicfp:soon",
                "title": "Soon Conf",
                "url": "https://example.com/soon",
                "summary": "Due soon",
                "deadline": now + timedelta(days=20),
                "image_url": "/static/favicon.svg",
            },
        )
        upsert_cfp_call(
            session,
            {
                "external_id": "wikicfp:far",
                "title": "Far Conf",
                "url": "https://example.com/far",
                "summary": "Too far",
                "deadline": now + timedelta(days=200),
            },
        )
        upsert_cfp_call(
            session,
            {
                "external_id": "wikicfp:past",
                "title": "Past Conf",
                "url": "https://example.com/past",
                "summary": "Expired",
                "deadline": now - timedelta(days=5),
            },
        )
        upsert_cfp_call(
            session,
            {
                "external_id": "wikicfp:nodl",
                "title": "No Deadline",
                "url": "https://example.com/nodl",
                "summary": "TBD",
                "deadline": None,
            },
        )
        rows = list_upcoming_cfps(session, within_days=90, limit=30)
        titles = [row.title for row in rows]
    assert titles == ["Soon Conf"]


def test_cfp_page_renders_upcoming(tmp_db):
    now = datetime.utcnow()
    with session_scope() as session:
        upsert_cfp_call(
            session,
            {
                "external_id": "wikicfp:page",
                "title": "Agentic AI Symposium",
                "url": "https://example.com/agentic",
                "summary": "Call for papers on autonomous agents.",
                "deadline": now + timedelta(days=40),
                "image_url": "/static/favicon.svg",
                "website_url": "https://agentic.example/cfp",
                "categories": "artificial intelligence",
                "location": "Singapore",
            },
        )
        upsert_cfp_call(
            session,
            {
                "external_id": "wikicfp:hidden",
                "title": "Hidden Far Away",
                "url": "https://example.com/far",
                "deadline": now + timedelta(days=180),
            },
        )
    from tests.conftest import login_admin

    client = login_admin(TestClient(app))
    page = client.get("/cfp")
    assert page.status_code == 200
    assert "Call for papers" in page.text
    assert "Agentic AI Symposium" in page.text
    assert "autonomous agents" in page.text
    assert "Singapore" in page.text
    assert "Conference website" in page.text
    assert "https://agentic.example/cfp" in page.text
    assert "WikiCFP →" not in page.text
    assert "Hidden Far Away" not in page.text
