"""Central SSRF protection: scheme, host, DNS, resolved IPs, and redirects."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from urllib.parse import urljoin, urlparse

from app.exceptions import UnsafeUrlError

ALLOWED_SCHEMES = {"https", "http"}
BLOCKED_SCHEMES = {"file", "ftp", "gopher", "data", "javascript", "blob", "intent"}
BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.goog",
    "metadata",
    "instance-data",
}
HTTP_ACADEMIC_HOSTS = {
    "arxiv.org",
    "export.arxiv.org",
    "dx.doi.org",
}

Resolver = Callable[[str], Sequence[str]]

_resolver: Resolver | None = None


def set_dns_resolver(resolver: Resolver | None) -> None:
    """Tests may inject a resolver that returns IP strings for a hostname."""
    global _resolver
    _resolver = resolver


def _default_resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    seen: set[str] = set()
    for info in infos:
        ip = str(info[4][0])
        if ip not in seen:
            seen.add(ip)
            addresses.append(ip)
    return addresses


def resolve_host(host: str) -> list[str]:
    resolver = _resolver or _default_resolve
    try:
        return list(resolver(host))
    except (socket.gaierror, OSError, ValueError) as exc:
        raise UnsafeUrlError(f"Hostname could not be resolved: {host}") from exc


def is_unsafe_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.version == 6:
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            return is_unsafe_ip(mapped)
        try:
            teredo = ip.teredo
        except Exception:
            teredo = None
        if teredo:
            return is_unsafe_ip(teredo[1])
        sixtofour = getattr(ip, "sixtofour", None)
        if sixtofour is not None:
            return is_unsafe_ip(sixtofour)

    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    if ip.version == 4:
        packed = int(ip)
        networks = (
            ipaddress.IPv4Network("0.0.0.0/8"),
            ipaddress.IPv4Network("10.0.0.0/8"),
            ipaddress.IPv4Network("100.64.0.0/10"),
            ipaddress.IPv4Network("127.0.0.0/8"),
            ipaddress.IPv4Network("169.254.0.0/16"),
            ipaddress.IPv4Network("172.16.0.0/12"),
            ipaddress.IPv4Network("192.0.0.0/24"),
            ipaddress.IPv4Network("192.0.2.0/24"),
            ipaddress.IPv4Network("192.168.0.0/16"),
            ipaddress.IPv4Network("198.18.0.0/15"),
            ipaddress.IPv4Network("198.51.100.0/24"),
            ipaddress.IPv4Network("203.0.113.0/24"),
            ipaddress.IPv4Network("224.0.0.0/4"),
            ipaddress.IPv4Network("240.0.0.0/4"),
            ipaddress.IPv4Network("255.255.255.255/32"),
        )
        return any(ip in net for net in networks) or packed == 0
    networks6 = (
        ipaddress.IPv6Network("::1/128"),
        ipaddress.IPv6Network("::/128"),
        ipaddress.IPv6Network("fc00::/7"),
        ipaddress.IPv6Network("fe80::/10"),
        ipaddress.IPv6Network("ff00::/8"),
        ipaddress.IPv6Network("2001:db8::/32"),
        ipaddress.IPv6Network("100::/64"),
    )
    return any(ip in net for net in networks6)


def _parse_literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    text = host.strip().strip("[]")
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        pass
    if text.lower().startswith("0x"):
        try:
            return ipaddress.IPv4Address(int(text, 16))
        except (ValueError, OverflowError):
            return None
    if text.isdigit():
        try:
            return ipaddress.IPv4Address(int(text, 10))
        except (ValueError, OverflowError):
            return None
    if text.startswith("0") and text.replace(".", "").isdigit():
        try:
            return ipaddress.IPv4Address(text)
        except ValueError:
            return None
    return None


def _hostname_from_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").strip().lower()
    if host.endswith("."):
        host = host[:-1]
    return host


def validate_outbound_url(
    url: str | None,
    *,
    prefer_https: bool = True,
    resolve_dns: bool = True,
    allow_http_hosts: set[str] | None = None,
) -> str:
    """Raise UnsafeUrlError if *url* must not be fetched. Returns the stripped URL."""
    if not url or not isinstance(url, str):
        raise UnsafeUrlError("Missing URL")
    text = url.strip()
    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme in BLOCKED_SCHEMES or scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"Blocked URL scheme: {scheme or 'missing'}")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URLs with embedded credentials are not allowed")
    host = _hostname_from_url(text)
    if not host:
        raise UnsafeUrlError("URL is missing a hostname")
    if host in BLOCKED_HOSTS:
        raise UnsafeUrlError(f"Blocked hostname: {host}")
    if host.endswith(".internal") or host.endswith(".localhost"):
        raise UnsafeUrlError(f"Blocked hostname: {host}")

    http_hosts = allow_http_hosts if allow_http_hosts is not None else HTTP_ACADEMIC_HOSTS
    if prefer_https and scheme != "https":
        allowed_http = host in http_hosts or host.endswith(".arxiv.org")
        if not allowed_http:
            raise UnsafeUrlError("HTTPS is required for outbound requests")

    literal = _parse_literal_ip(host)
    if literal is not None:
        if is_unsafe_ip(literal):
            raise UnsafeUrlError(f"Blocked address: {host}")
        return text

    if resolve_dns:
        resolve_and_validate_host(host)
    return text


def resolve_and_validate_host(host: str) -> list[str]:
    host = (host or "").strip().strip("[]").lower()
    if not host:
        raise UnsafeUrlError("URL is missing a hostname")
    if host in BLOCKED_HOSTS:
        raise UnsafeUrlError(f"Blocked hostname: {host}")
    literal = _parse_literal_ip(host)
    if literal is not None:
        if is_unsafe_ip(literal):
            raise UnsafeUrlError(f"Blocked address: {host}")
        return [str(literal)]
    addresses = resolve_host(host)
    if not addresses:
        raise UnsafeUrlError(f"Hostname could not be resolved: {host}")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UnsafeUrlError(f"Invalid resolved address for {host}") from exc
        if is_unsafe_ip(ip):
            raise UnsafeUrlError(f"Hostname {host} resolved to a blocked address")
    return addresses


def next_redirect_url(current_url: str, location: str | None) -> str:
    if not location or not str(location).strip():
        raise UnsafeUrlError("Redirect missing Location header")
    return urljoin(current_url, str(location).strip())


def is_safe_url(
    url: str | None,
    *,
    prefer_https: bool = True,
    resolve_dns: bool = True,
) -> bool:
    try:
        validate_outbound_url(url, prefer_https=prefer_https, resolve_dns=resolve_dns)
        return True
    except UnsafeUrlError:
        return False
