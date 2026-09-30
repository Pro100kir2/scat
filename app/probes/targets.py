"""What probes may connect to, and how scan results become probe targets.

Targets come from public subscription lists, i.e. from anyone. A poisoned list could name
10.0.0.1, a router admin page or a cloud metadata address, and the probe, which sits
inside a volunteer's home network, would connect there. So:

* the backend resolves hostnames itself and hands probes IP literals only (no DNS on the
  probe, so no DNS rebinding and no split-horizon surprises);
* every address must be globally routable: loopback, private, link-local, CGNAT, ULA,
  multicast, reserved, documentation, NAT64/6to4-wrapped private space and metadata
  addresses are all rejected;
* ports and server names are validated; mail/SMB ports are refused outright.

The agent re-validates everything (defence in depth), so a compromised backend still
cannot turn probes into a LAN scanner.
"""
import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Literal, Optional
from urllib.parse import urlsplit

CheckType = Literal["TCP_CONNECT", "TLS_CONNECT", "HTTP_GET"]
CHECK_TYPES: tuple[CheckType, ...] = ("TCP_CONNECT", "TLS_CONNECT", "HTTP_GET")

MIN_TIMEOUT_MS = 500
MAX_TIMEOUT_MS = 10_000
DEFAULT_TIMEOUT_MS = 5_000
# Abuse magnets: probing these from home networks gets volunteers into trouble.
BLOCKED_PORTS = frozenset({0, 25, 135, 136, 137, 138, 139, 445, 465, 587})
MAX_HTTP_PATH = 256
DNS_TIMEOUT = 5.0

_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))*\.?$", re.IGNORECASE)
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")

# Ranges ipaddress does not flag (or flags inconsistently across Python versions).
_EXTRA_BLOCKED = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "100.64.0.0/10",  # carrier-grade NAT
        "192.0.0.0/24",
        "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",  # documentation
        "198.18.0.0/15",  # benchmarking
        "240.0.0.0/4",
        "255.255.255.255/32",
        "64:ff9b:1::/48",  # local-use NAT64
        "100::/64",  # discard-only
        "2001:db8::/32",  # documentation
        "fc00::/7",  # unique local
        "fe80::/10",
        "fec0::/10",  # deprecated site-local
    )
]


class TargetRejected(ValueError):
    """The target is not something a probe may ever connect to."""


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> Optional[ipaddress.IPv4Address]:
    """IPv4 hidden inside an IPv6 address (mapped, NAT64, 6to4, Teredo)."""
    if ip.ipv4_mapped:
        return ip.ipv4_mapped
    if ip in ipaddress.ip_network("64:ff9b::/96"):
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.sixtofour:
        return ip.sixtofour
    if ip.teredo:
        return ip.teredo[1]
    return None


def is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.scope_id:
            return False
        inner = _embedded_ipv4(ip)
        if inner is not None and not is_public_ip(str(inner)):
            return False
    if (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified or not ip.is_global
    ):
        return False
    return not any(ip.version == net.version and ip in net for net in _EXTRA_BLOCKED)


def validate_ip(value: str) -> str:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        raise TargetRejected(f"not an IP literal: {value!r}") from None
    if not is_public_ip(str(ip)):
        raise TargetRejected(f"non-public address: {ip}")
    return str(ip)


def validate_port(port: int) -> int:
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise TargetRejected(f"invalid port: {port!r}")
    if port in BLOCKED_PORTS:
        raise TargetRejected(f"port {port} is not allowed")
    return port


def validate_hostname(name: str) -> str:
    """A DNS name usable as SNI / Host header. IP literals and local names are refused."""
    name = (name or "").strip().rstrip(".").lower()
    if not name or not _HOSTNAME_RE.match(name):
        raise TargetRejected(f"invalid hostname: {name!r}")
    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        raise TargetRejected("an IP address is not a server name")
    if "." not in name or name.endswith((".local", ".localhost", ".internal", ".lan", ".home.arpa")) or name == "localhost":
        raise TargetRejected(f"non-public hostname: {name!r}")
    return name


def validate_timeout(timeout_ms: int) -> int:
    if not isinstance(timeout_ms, int) or not MIN_TIMEOUT_MS <= timeout_ms <= MAX_TIMEOUT_MS:
        raise TargetRejected(f"timeout_ms must be {MIN_TIMEOUT_MS}..{MAX_TIMEOUT_MS}")
    return timeout_ms


def validate_path(path: str) -> str:
    if not path or len(path) > MAX_HTTP_PATH or not _PATH_RE.match(path) or "//" in path or ".." in path:
        raise TargetRejected(f"invalid HTTP path: {path!r}")
    return path


@dataclass(frozen=True)
class Target:
    """One network check. Frozen and fully validated: the only shape a job can take."""

    type: CheckType
    ip: str
    port: int
    server_name: Optional[str] = None  # TLS SNI / HTTP Host
    path: Optional[str] = None  # HTTP_GET only
    tls: bool = False  # HTTP_GET over TLS

    def __post_init__(self):
        if self.type not in CHECK_TYPES:
            raise TargetRejected(f"unknown check type {self.type!r}")
        validate_ip(self.ip)
        validate_port(self.port)
        if self.server_name is not None:
            validate_hostname(self.server_name)
        if self.type == "TCP_CONNECT" and (self.server_name or self.path or self.tls):
            raise TargetRejected("TCP_CONNECT takes only an address and a port")
        if self.type == "TLS_CONNECT" and (self.path or not self.server_name):
            raise TargetRejected("TLS_CONNECT needs a server name and no path")
        if self.type == "HTTP_GET":
            if not self.server_name:
                raise TargetRejected("HTTP_GET needs a Host name")
            validate_path(self.path or "")

    @property
    def key(self) -> str:
        """Identity for deduplication and result reuse across configs and rounds."""
        parts = [self.type, self.ip, str(self.port), self.server_name or ""]
        if self.type == "HTTP_GET":
            parts += ["https" if self.tls else "http", self.path or ""]
        return "|".join(parts)

    def job_payload(self, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict:
        payload = {"type": self.type, "host": self.ip, "port": self.port, "timeout_ms": validate_timeout(timeout_ms)}
        if self.server_name:
            payload["server_name"] = self.server_name
        if self.type == "HTTP_GET":
            payload["path"] = self.path
            payload["tls"] = self.tls
        return payload

    @classmethod
    def from_payload(cls, payload: dict) -> "Target":
        return cls(
            type=payload["type"],
            ip=payload["host"],
            port=payload["port"],
            server_name=payload.get("server_name"),
            path=payload.get("path"),
            tls=bool(payload.get("tls", False)),
        )


async def resolve_public(host: str, timeout: float = DNS_TIMEOUT) -> str:
    """One public address for `host` (IPv4 preferred: most probes have no IPv6).

    An IP literal is only validated. For names, every returned address must be public:
    a name that also resolves to a private address is refused as a whole.
    """
    host = (host or "").strip().strip("[]")
    try:
        return validate_ip(host)
    except TargetRejected:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise
    name = validate_hostname(host)
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(name, None, type=socket.SOCK_STREAM), timeout=timeout
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise TargetRejected(f"cannot resolve {name}: {exc}") from None
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise TargetRejected(f"{name} has no addresses")
    if not all(is_public_ip(a) for a in addresses):
        raise TargetRejected(f"{name} resolves to a non-public address")
    v4 = [a for a in addresses if ":" not in a]
    return (v4 or addresses)[0]


async def target_for_node(node: dict) -> Target:
    """The reachability check that fits a scanned VLESS config.

    TLS and REALITY configs get a TLS handshake with the config's SNI: that is what DPI
    blocks on, so it says more than a bare TCP connect. Plain configs get TCP_CONNECT.
    """
    address = str(node.get("address") or "")
    port = int(node.get("port") or 0)
    validate_port(port)
    ip = await resolve_public(address)
    security = (node.get("security") or "none").lower()
    if security in ("tls", "reality"):
        sni = node.get("sni") or ""
        if not sni:
            try:
                sni = validate_hostname(address)
            except TargetRejected:
                sni = ""
        if sni:
            return Target("TLS_CONNECT", ip, port, server_name=validate_hostname(sni))
    return Target("TCP_CONNECT", ip, port)


async def control_target(url: str) -> Target:
    """HTTP_GET target for the control URL each probe fetches to prove its own network works."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise TargetRejected(f"invalid control URL: {url!r}")
    tls = parts.scheme == "https"
    port = parts.port or (443 if tls else 80)
    host = validate_hostname(parts.hostname)
    ip = await resolve_public(host)
    return Target("HTTP_GET", ip, validate_port(port), server_name=host, path=parts.path or "/", tls=tls)
