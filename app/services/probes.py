"""What "working" means.

A config is only worth showing if a real client could use it, so every probe goes
through the local SOCKS port Xray opens for the node and:

* verifies TLS end to end: the HTTPS request travels *inside* the tunnel, so a
  certificate error means the traffic was intercepted, whatever the proxy protocol;
* checks the body, not just the status code (captive pages answer 200 too);
* checks the exit IP differs from ours (otherwise traffic never left through the proxy);
* reaches a second, independent site (some servers only let a few hosts through);
* downloads real payload (DPI often lets tiny requests through and stalls later).
"""
import asyncio
import ssl
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp
import certifi
from aiohttp_socks import ProxyConnector

from app import config

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
HEADERS = {"User-Agent": "Mozilla/5.0 (Skat scanner)", "Accept": "*/*"}


@dataclass
class Fetch:
    status: Optional[int] = None
    body: bytes = b""
    elapsed_ms: Optional[float] = None
    error: str = ""


async def _fetch(port: Optional[int], url: str, timeout: float, max_bytes: int) -> Fetch:
    connector = (
        ProxyConnector.from_url(f"socks5://127.0.0.1:{port}", rdns=True, ssl=SSL_CONTEXT)
        if port is not None
        else aiohttp.TCPConnector(ssl=SSL_CONTEXT)
    )
    start = time.perf_counter()
    async with aiohttp.ClientSession(
        connector=connector, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=timeout)
    ) as session:
        async with session.get(url, allow_redirects=False) as response:
            body = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                body += chunk
                if len(body) >= max_bytes:
                    break
            return Fetch(response.status, bytes(body), (time.perf_counter() - start) * 1000)


async def fetch(port: Optional[int], url: str, timeout: float, max_bytes: int = 16 * 1024) -> Fetch:
    """GET through the node's SOCKS port (or directly when port is None). Never raises."""
    try:
        # Hard ceiling: python-socks ignores aiohttp's timeout during the SOCKS handshake.
        return await asyncio.wait_for(_fetch(port, url, timeout, max_bytes), timeout=timeout + 0.5)
    except asyncio.TimeoutError:
        return Fetch(error="timeout")
    except ssl.SSLError:
        return Fetch(error="TLS verification failed (traffic intercepted)")
    except aiohttp.ClientConnectorCertificateError:
        return Fetch(error="TLS verification failed (traffic intercepted)")
    except Exception as exc:  # network errors of every flavour mean "not usable"
        return Fetch(error=type(exc).__name__)


def parse_trace(body: bytes) -> dict[str, str]:
    lines = body.decode("utf-8", errors="ignore").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


@dataclass
class TraceResult:
    ok: bool
    latency_ms: Optional[float] = None
    exit_ip: str = ""
    country: str = ""
    error: str = ""


async def trace(port: Optional[int], direct_ip: Optional[str] = None, timeout: float = 0) -> TraceResult:
    """Cloudflare's trace page: proves the tunnel works and tells us where it exits."""
    result = await fetch(port, config.TRACE_URL, timeout or config.TIMEOUT)
    if result.status is None:
        return TraceResult(False, error=result.error)
    if result.status != 200:
        return TraceResult(False, error=f"HTTP {result.status}")
    data = parse_trace(result.body)
    if data.get("h") != config.TRACE_HOST or not data.get("ip"):
        return TraceResult(False, error="unexpected trace content")
    if direct_ip and data["ip"] == direct_ip:
        return TraceResult(False, error="not tunnelled: exit IP is the scanner's own")
    return TraceResult(True, result.elapsed_ms, data["ip"], data.get("loc", "").upper())


async def reach(port: int) -> bool:
    """A second, unrelated destination must work too."""
    result = await fetch(port, config.REACH_URL, config.TIMEOUT + 1)
    return result.status == config.REACH_STATUS


async def download(port: int, size: int, timeout: float) -> Optional[float]:
    """Download `size` bytes; return Mbit/s, or None if the transfer did not complete."""
    result = await fetch(port, f"{config.SPEED_URL}?bytes={size}", timeout, max_bytes=size)
    if result.status != 200 or len(result.body) < size or not result.elapsed_ms:
        return None
    return size * 8 / 1_000_000 / (result.elapsed_ms / 1000)


async def direct_exit_ip() -> Optional[str]:
    """Our own public IP, to detect nodes that never actually tunnel."""
    result = await trace(None, timeout=10)
    return result.exit_ip or None
