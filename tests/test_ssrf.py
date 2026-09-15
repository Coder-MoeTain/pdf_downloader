import ipaddress

import pytest

from app.exceptions import UnsafeUrlError
from app.security.ssrf import is_safe_url, is_unsafe_ip, set_dns_resolver, validate_outbound_url
from app.utils.http import AsyncHttpClient, HttpError


def test_url_safety_schemes_and_localhost():
    assert is_safe_url("https://arxiv.org/pdf/1234.5678.pdf", resolve_dns=False)
    assert not is_safe_url("file:///etc/passwd", resolve_dns=False)
    assert not is_safe_url("javascript:alert(1)", resolve_dns=False)
    assert not is_safe_url("ftp://example.com/file", resolve_dns=False)
    assert not is_safe_url("http://127.0.0.1/secret", resolve_dns=False)
    assert not is_safe_url("http://localhost/secret", resolve_dns=False)
    assert not is_safe_url("http://169.254.169.254/", resolve_dns=False)
    assert not is_safe_url("http://[::1]/", resolve_dns=False)
    assert not is_safe_url("http://10.0.0.5/", resolve_dns=False)


def test_encoded_and_metadata_ips():
    assert not is_safe_url("http://2130706433/", resolve_dns=False)
    with pytest.raises(UnsafeUrlError):
        validate_outbound_url("http://169.254.169.254/", prefer_https=False, resolve_dns=False)
    with pytest.raises(UnsafeUrlError):
        validate_outbound_url("http://[::ffff:127.0.0.1]/", prefer_https=False, resolve_dns=False)


def test_dns_resolving_to_private_ip():
    set_dns_resolver(lambda host: ["127.0.0.1"])
    try:
        with pytest.raises(UnsafeUrlError):
            validate_outbound_url("https://evil.example", prefer_https=True, resolve_dns=True)
    finally:
        set_dns_resolver(None)


@pytest.mark.asyncio
async def test_redirect_public_to_private_blocked():
    set_dns_resolver(lambda host: ["93.184.216.34"] if host == "public.example" else ["127.0.0.1"])
    try:

        class DummyResponse:
            def __init__(self, status, headers, url):
                self.status_code = status
                self.headers = headers
                self.url = url
                self.is_redirect = status in {301, 302, 303, 307, 308}
                self.content = b"{}"
                self.text = "{}"

            def json(self):
                return {}

        class DummyClient:
            async def request(self, method, url, **kwargs):
                if "public.example" in str(url):
                    return DummyResponse(302, {"location": "http://127.0.0.1/secret"}, url)
                return DummyResponse(200, {}, url)

            async def aclose(self):
                return None

        client = AsyncHttpClient()
        client._client = DummyClient()  # type: ignore[assignment]
        with pytest.raises(HttpError):
            await client.request("GET", "https://public.example/paper")
    finally:
        set_dns_resolver(None)


def test_private_ipv6_flagged():
    assert is_unsafe_ip(ipaddress.ip_address("::1"))
    assert is_unsafe_ip(ipaddress.ip_address("fc00::1"))
    assert is_unsafe_ip(ipaddress.ip_address("fe80::1"))
