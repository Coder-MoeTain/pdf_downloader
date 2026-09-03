from app.config import parse_size
from app.utils.http import http_error_detail
from app.utils.security import is_safe_url
import httpx


def test_parse_size():
    assert parse_size("50MB") == 50 * 1024 * 1024
    assert parse_size("2GB") == 2 * 1024 ** 3
    assert parse_size(100) == 100


def test_url_safety():
    assert is_safe_url("https://arxiv.org/pdf/1234.5678.pdf")
    assert not is_safe_url("file:///etc/passwd")
    assert not is_safe_url("javascript:alert(1)")
    assert not is_safe_url("http://127.0.0.1/secret")


def test_http_error_detail_strips_ieee_html():
    response = httpx.Response(403, text="<h1>Developer Inactive</h1>")
    assert http_error_detail(response) == "Developer Inactive"
