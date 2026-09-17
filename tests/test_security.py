import httpx
import pytest

from app.config import parse_size
from app.utils.http import HttpError, http_error_detail, parse_json_response
from app.utils.security import is_safe_url


def test_parse_size():
    assert parse_size("50MB") == 50 * 1024 * 1024
    assert parse_size("2GB") == 2 * 1024**3
    assert parse_size(100) == 100


def test_url_safety():
    assert is_safe_url("https://arxiv.org/pdf/1234.5678.pdf")
    assert not is_safe_url("file:///etc/passwd")
    assert not is_safe_url("javascript:alert(1)")
    assert not is_safe_url("http://127.0.0.1/secret")


def test_http_error_detail_strips_ieee_html():
    response = httpx.Response(403, text="<h1>Developer Inactive</h1>")
    assert http_error_detail(response) == "Developer Inactive"


def test_parse_json_response_empty_body():
    with pytest.raises(HttpError, match="Empty JSON response"):
        parse_json_response(httpx.Response(200, content=b""), "https://www.osti.gov/api/v1/records")


def test_parse_json_response_html_body():
    with pytest.raises(HttpError, match="Invalid JSON"):
        parse_json_response(
            httpx.Response(200, text="<html><title>Blocked</title></html>"),
            "https://www.osti.gov/api/v1/records",
        )


def test_parse_json_response_bot_challenge():
    with pytest.raises(HttpError, match="anti-bot challenge"):
        parse_json_response(
            httpx.Response(
                200,
                text="<html><body>Making sure you're not a bot!</body></html>",
            ),
            "https://dblp.org/search/publ/api",
        )


def test_csp_allows_same_origin_pdf_iframes():
    from app.security.headers import security_headers

    headers = security_headers(production=True, https=True)
    csp = headers["Content-Security-Policy"]
    assert "frame-src 'self'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "frame-ancestors 'none'" not in csp
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    script_src = csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "unsafe-inline" not in script_src


def test_library_preview_csp_and_theme_script(auth_client):
    page = auth_client.get("/library")
    assert page.status_code == 200
    assert "/static/theme-init.js" in page.text
    assert "document.documentElement.setAttribute" not in page.text
    csp = page.headers["content-security-policy"]
    assert "frame-src 'self'" in csp
    assert "frame-ancestors 'self'" in csp
    assert page.headers["x-frame-options"] == "SAMEORIGIN"
