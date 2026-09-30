"""Fetching admin-configurable URLs (VLESS sources) without SSRF.

A source URL is typed into a browser form, so it must never make the backend reach itself
(127.0.0.1:8000), the Docker network, the host's LAN, or a cloud metadata service.
Every request:

* allows http/https only, no user:password@, no fragments smuggling hosts, ≤ 2048 chars;
* resolves the host *here*, refuses it if **any** address is non-public (the same policy
  probes use: loopback, RFC 1918, link-local incl. 169.254.169.254, CGNAT, ULA, multicast,
  reserved, documentation, IPv4 wrapped in IPv6...), and then connects to exactly those
  vetted addresses (a pinned resolver), so DNS rebinding between check and connect is
  impossible. Numeric tricks (2130706433, 0x7f.1, 017700000001) are resolved first and so
  caught by the same check;
* never follows redirects blindly: each Location is re-validated the same way (≤ N hops);
* ignores proxy environment variables, caps the body size and the total time.
"""
import asyncio
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

import aiohttp
import certifi
from aiohttp.abc import AbstractResolver

from app import config
from app.probes import targets

MAX_URL_LENGTH = 2048
REDIRECT_CODES = (301, 302, 303, 307, 308)
BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa", ".localdomain")
HEADERS = {"User-Agent": "Mozilla/5.0 VLESS-Scanner/2.0", "Accept": "*/*"}
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def address_allowed(ip: str) -> bool:
    """The address policy. Tests replace it to reach their own loopback servers."""
    return targets.is_public_ip(ip)


class UnsafeURL(ValueError):
    """The URL points somewhere the backend must not go, or is not a plain web URL."""


@dataclass
class FetchResult:
    url: str
    ok: bool
    text: str = ""
    status: Optional[int] = None
    error: str = ""
    bytes: int = 0
    duration_ms: float = 0.0
    final_url: str = ""
    redirects: list[str] = field(default_factory=list)


def check_url(url: str) -> tuple[str, str, int]:
    """Syntax and literal-address checks that need no DNS. Returns (scheme, host, port)."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise UnsafeURL("URL is empty or too long")
    if any(c in url for c in "\r\n\t\\ ") or not url.isprintable():
        raise UnsafeURL("URL contains whitespace or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeURL(f"malformed URL: {exc}") from None
    if parts.scheme not in ("http", "https"):
        raise UnsafeURL("only http:// and https:// URLs are allowed")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UnsafeURL("credentials in URLs are not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURL("URL has no host")
    if port == 0:
        raise UnsafeURL("port 0 is not allowed")
    port = port or (443 if parts.scheme == "https" else 80)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host == "localhost" or host.endswith(BLOCKED_HOST_SUFFIXES) or "." not in host:
            raise UnsafeURL(f"{host} is not a public host name") from None
    else:
        if not address_allowed(str(ip)):
            raise UnsafeURL(f"{host} is not a public address")
    return parts.scheme, host, port


async def resolve_checked(host: str, port: int, timeout: float = 10.0) -> list[str]:
    """All addresses of `host`, each one allowed; any non-public answer refuses the host."""
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout)
    except (OSError, asyncio.TimeoutError) as exc:
        raise UnsafeURL(f"cannot resolve {host}: {type(exc).__name__}") from None
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise UnsafeURL(f"{host} has no addresses")
    bad = [a for a in addresses if not address_allowed(a)]
    if bad:
        raise UnsafeURL(f"{host} resolves to a non-public address ({bad[0]})")
    return addresses


async def validate_url(url: str) -> str:
    """Full check including DNS, for admin forms. Returns the URL unchanged."""
    _, host, port = check_url(url)
    await resolve_checked(host, port)
    return url


class _PinnedResolver(AbstractResolver):
    """Answers only with addresses validated moments ago; anything else is refused."""

    def __init__(self):
        self.pinned: dict[str, list[str]] = {}

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        addresses = self.pinned.get(host.rstrip(".").lower())
        if not addresses:
            raise OSError(f"{host} was not validated")
        return [{"hostname": host, "host": ip, "port": port, "proto": 0, "flags": socket.AI_NUMERICHOST,
                 "family": socket.AF_INET6 if ":" in ip else socket.AF_INET} for ip in addresses]

    async def close(self) -> None:
        pass


async def fetch_text(url: str, *, timeout: float, max_bytes: int = 0, max_redirects: Optional[int] = None,
                     clock: Callable[[], float] = time.perf_counter) -> FetchResult:
    """GET a source safely. Never raises: problems come back as FetchResult(ok=False)."""
    max_bytes = max_bytes or config.SOURCE_MAX_BYTES
    max_redirects = config.SOURCE_MAX_REDIRECTS if max_redirects is None else max_redirects
    started = clock()
    result = FetchResult(url=url, ok=False)
    resolver = _PinnedResolver()
    connector = aiohttp.TCPConnector(resolver=resolver, ssl=SSL_CONTEXT, use_dns_cache=False, limit=4)
    client_timeout = aiohttp.ClientTimeout(total=timeout, connect=min(timeout, 10), sock_read=timeout)
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=client_timeout, trust_env=False,
                                         headers=HEADERS) as session:
            current = url
            for hop in range(max_redirects + 1):
                _, host, port = check_url(current)
                resolver.pinned[host] = await resolve_checked(host, port)
                async with session.get(current, allow_redirects=False) as response:
                    result.status = response.status
                    if response.status in REDIRECT_CODES:
                        location = response.headers.get("Location")
                        if not location or hop == max_redirects:
                            raise UnsafeURL("too many redirects" if location else "redirect without Location")
                        current = urljoin(current, location)
                        result.redirects.append(current)
                        continue
                    if not 200 <= response.status < 300:
                        result.error = f"HTTP {response.status}"
                        return result
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        body += chunk
                        if len(body) > max_bytes:
                            raise UnsafeURL(f"response larger than {max_bytes // (1024 * 1024)} MB")
                    result.bytes = len(body)
                    result.text = body.decode(response.get_encoding() if response.charset else "utf-8", errors="ignore")
                    result.final_url = current
                    result.ok = True
                    return result
    except UnsafeURL as exc:
        result.error = f"blocked: {exc}"
    except asyncio.TimeoutError:
        result.error = "timeout"
    except aiohttp.ClientConnectorCertificateError:
        result.error = "TLS certificate verification failed"
    except aiohttp.ClientConnectorError as exc:
        result.error = f"connection failed: {type(exc.os_error).__name__ if exc.os_error else 'error'}"
    except Exception as exc:  # every network failure is just "this source failed"
        result.error = f"{type(exc).__name__}: {str(exc)[:160]}"
    finally:
        result.duration_ms = round((clock() - started) * 1000, 1)
    return result
