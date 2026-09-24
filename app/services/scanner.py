import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import signal
import ssl
import sys
import tempfile
import time
import urllib.request
import zipfile
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from itertools import zip_longest
from pathlib import Path
from statistics import median, pstdev
from typing import Optional, Dict, Any, List
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

import aiohttp
import certifi

from app import config
from app.services import probes

DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")
logger = logging.getLogger("skat.scanner")


class XrayStartError(RuntimeError):
    """Xray exited during startup, usually because it rejected the generated config."""


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class VLESSNode:
    """One VLESS config. The fields hold exactly what the scanner tests; `link` serializes them."""

    source_link: str  # as found in the source; never shown to users
    fingerprint: str
    uuid: str
    address: str
    port: int
    name: str = ""
    encryption: str = "none"
    flow: str = ""
    network: str = "tcp"
    security: str = "none"
    sni: str = ""
    fingerprint_tls: str = ""
    alpn: list[str] = field(default_factory=list)
    allow_insecure: bool = False
    public_key: str = ""
    short_id: str = ""
    spider_x: str = ""
    path: str = ""
    host: str = ""
    service_name: str = ""
    mode: str = ""  # grpc: "multi" or ""; xhttp: its mode
    link: str = ""  # the share link users get: build_vless_uri() of the tested fields
    params: dict = field(default_factory=dict)
    local_port: int = 0
    latency_samples: list[float] = field(default_factory=list)
    latency_ms: Optional[float] = None
    status: Optional[int] = None
    success: bool = False
    stable: bool = False
    error: str = ""
    attempts: int = 0
    label_country: str = ""  # from the flag emoji in the source label; only a hint
    exit_ip: str = ""  # measured through the tunnel
    exit_country: str = ""  # measured through the tunnel (Cloudflare trace "loc")
    speed_mbps: Optional[float] = None
    jitter_ms: Optional[float] = None
    source_index: int = 0


@dataclass
class SourceResult:
    url: str
    success: bool
    vless_count: int = 0
    error: str = ""


# ============================================================
# HELPER PARSING FUNCTIONS
# ============================================================

SUPPORTED_NETWORKS = {
    "tcp", "raw", "ws", "websocket", "grpc", "xhttp", "splithttp", "httpupgrade", "http-upgrade"
}

NETWORK_ALIASES = {
    "raw": "tcp",
    "tcp": "tcp",
    "websocket": "ws",
    "ws": "ws",
    "splithttp": "xhttp",
    "xhttp": "xhttp",
    "http-upgrade": "httpupgrade",
    "httpupgrade": "httpupgrade",
    "grpc": "grpc",
}

# Values Xray accepts; anything else makes Xray reject the whole batch config.
XRAY_FINGERPRINTS = {
    "chrome", "firefox", "safari", "ios", "android", "edge", "360", "qq",
    "random", "randomized", "unsafe",
}
XRAY_FLOWS = {"", "xtls-rprx-vision", "xtls-rprx-vision-udp443"}
XRAY_ALPN = {"h2", "http/1.1", "h3"}
SHORT_ID_RE = re.compile(r"^(?:[0-9a-fA-F]{2}){0,8}$")

VLESS_RE = re.compile(
    r"vless://[^\s\"'<>]*?(?=vless://|[\s\"'<>]|$)",
    re.IGNORECASE,
)


def clean_name(value: str) -> str:
    value = unquote(value or "")
    value = re.sub(r"[\x00-\x1f\x7f]", "", value)
    return value.strip()[:100]


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def flatten_query(query: str) -> dict:
    parsed = parse_qs(query, keep_blank_values=True)
    return {key: (values[0] if values else "") for key, values in parsed.items()}


def q(params: dict, *names: str, default: str = "") -> str:
    for name in names:
        value = params.get(name)
        if value is not None and value != "":
            return str(value)
    return default


def bool_value(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_network(value: str) -> str:
    value = str(value or "").strip().lower()
    value = unquote(value).strip().lower()

    if "vless://" in value:
        prefix = value.split("vless://", 1)[0].strip()
        if prefix in NETWORK_ALIASES:
            value = prefix
        else:
            value = ""

    if "#" in value:
        value = value.split("#", 1)[0].strip()

    for separator in ("@", "&", "?"):
        if separator in value:
            value = value.split(separator, 1)[0].strip()

    return NETWORK_ALIASES.get(value, value)


def clean_vless_candidate(value: str) -> str:
    value = value.strip()
    value = value.rstrip(" \t\r\n.,;)]}\"'")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    # Links scraped from HTML pages keep "&amp;" and would silently lose every parameter after it.
    return value.replace("&amp;", "&")


def extract_vless(text: str) -> list[str]:
    if not text:
        return []
    result = []
    for match in VLESS_RE.finditer(text):
        value = clean_vless_candidate(match.group(0))
        if value.lower().startswith("vless://"):
            result.append(value)
    return result


def decode_base64(text: str) -> Optional[str]:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return None
    candidates = [compact, compact.replace("-", "+").replace("_", "/")]
    for candidate in candidates:
        padding = "=" * (-len(candidate) % 4)
        try:
            decoded = base64.b64decode(candidate + padding, validate=False)
            result = decoded.decode("utf-8", errors="ignore")
            if "vless://" in result.lower():
                return result
        except Exception:
            continue
    return None


def extract_source_nodes(text: str) -> list[str]:
    direct = extract_vless(text)
    if direct:
        return direct
    decoded = decode_base64(text)
    if decoded:
        return extract_vless(decoded)
    return []


_FLAG_RE = re.compile("([\U0001F1E6-\U0001F1FF]{2})")


def country_from_name(name: str) -> Optional[str]:
    """ISO 3166 code from the first flag emoji in a source label, e.g. 🇩🇪 → DE."""
    match = _FLAG_RE.search(name or "")
    if not match:
        return None
    return "".join(chr(ord(ch) - 0x1F1E6 + ord("A")) for ch in match.group(1))


def _is_x25519_key(value: str) -> bool:
    try:
        return len(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))) == 32
    except (binascii.Error, ValueError):
        return False


def parse_vless(url: str) -> VLESSNode:
    url = clean_vless_candidate(url)
    parts = urlsplit(url)

    if parts.scheme.lower() != "vless":
        raise ValueError("invalid scheme")
    if not parts.username:
        raise ValueError("missing UUID")
    if not parts.hostname:
        raise ValueError("missing address")
    if not parts.port:
        raise ValueError("missing port")

    uuid = unquote(parts.username).strip()
    if not uuid:
        raise ValueError("empty UUID")

    params = flatten_query(parts.query)
    raw_network = q(params, "type", "network", default="tcp")
    network = normalize_network(raw_network) or "tcp"
    if network not in {"tcp", "ws", "grpc", "xhttp", "httpupgrade"}:
        raise ValueError(f"unsupported transport: {raw_network!r}")

    security = q(params, "security", default="none").strip().lower()
    if security not in {"tls", "reality"}:
        security = "none"

    encryption = q(params, "encryption", default="none").strip() or "none"
    if encryption.lower().startswith("mlkem"):
        # VLESS Encryption: the scanner only speaks "none", so it could never test this config.
        raise ValueError("unsupported VLESS encryption")
    flow = q(params, "flow").strip().lower()
    if flow not in XRAY_FLOWS:
        raise ValueError(f"unsupported flow: {flow!r}")
    sni = q(params, "sni", "servername", "serverName").strip()
    fingerprint_tls = q(params, "fp", "fingerprint").strip().lower()
    if fingerprint_tls not in XRAY_FINGERPRINTS:
        fingerprint_tls = ""

    alpn_raw = q(params, "alpn")
    alpn = [x.strip().lower() for x in alpn_raw.split(",") if x.strip().lower() in XRAY_ALPN]

    allow_insecure = bool_value(q(params, "allowInsecure", "allowinsecure", default="false"))
    public_key = q(params, "pbk", "publicKey").strip()
    short_id = q(params, "sid", "shortId").strip()
    if security == "reality":
        if not SHORT_ID_RE.match(short_id):
            raise ValueError("invalid REALITY shortId")
        if public_key and not _is_x25519_key(public_key):
            raise ValueError("invalid REALITY publicKey")
    spider_x = q(params, "spx", "spiderX")
    path = q(params, "path")
    host = q(params, "host")
    service_name = q(params, "serviceName")
    mode = q(params, "mode").strip()

    # Keep only what Xray actually uses for this transport and security, so the node,
    # the tested Xray config and the share link describe the same thing.
    if security == "none":
        sni = fingerprint_tls = ""
    if security != "tls":
        alpn, allow_insecure = [], False
    if security == "reality":
        fingerprint_tls = fingerprint_tls or "chrome"  # Xray needs one for REALITY
    else:
        public_key = short_id = spider_x = ""
    if network not in {"ws", "xhttp", "httpupgrade"}:
        path = host = ""
    if network != "grpc":
        service_name = ""
    if network == "grpc":
        mode = "multi" if mode.lower() == "multi" else ""
    elif network != "xhttp":
        mode = ""
    name = clean_name(parts.fragment)
    label_country = country_from_name(name)

    identity = {
        "uuid": uuid,
        "address": parts.hostname.lower(),
        "port": parts.port,
        "network": network,
        "security": security,
        "flow": flow,
        "sni": sni,
        "fp": fingerprint_tls,
        "alpn": alpn,
        "insecure": allow_insecure,
        "pbk": public_key,
        "sid": short_id,
        "spx": spider_x,
        "path": path,
        "host": host,
        "serviceName": service_name,
        "mode": mode,
    }

    fingerprint = short_hash(json.dumps(identity, sort_keys=True, ensure_ascii=False))

    node = VLESSNode(
        source_link=url,
        fingerprint=fingerprint,
        uuid=uuid,
        address=parts.hostname,
        port=parts.port,
        name=name,
        flow=flow,
        network=network,
        security=security,
        sni=sni,
        fingerprint_tls=fingerprint_tls,
        alpn=alpn,
        allow_insecure=allow_insecure,
        public_key=public_key,
        short_id=short_id,
        spider_x=spider_x,
        path=path,
        host=host,
        service_name=service_name,
        mode=mode,
        params=params,
        label_country=label_country or "",
    )
    node.link = build_vless_uri(node)
    return node


def build_vless_uri(node: VLESSNode) -> str:
    """Share link for exactly the configuration the scanner tests (see build_outbound).

    Every value is percent-encoded, so clients that parse URIs strictly accept it too.
    """
    params = {"encryption": "none", "type": node.network, "security": node.security}
    optional = {
        "flow": node.flow,
        "path": node.path,
        "host": node.host,
        "serviceName": node.service_name,
        "mode": node.mode,
        "sni": node.sni,
        "fp": node.fingerprint_tls,
        "alpn": ",".join(node.alpn),
        "allowInsecure": "1" if node.allow_insecure else "",
        "pbk": node.public_key,
        "sid": node.short_id,
        "spx": node.spider_x,
    }
    params.update({key: value for key, value in optional.items() if value})
    host = f"[{node.address}]" if ":" in node.address else node.address
    link = f"vless://{quote(node.uuid, safe='')}@{host}:{node.port}?{urlencode(params, quote_via=quote, safe='')}"
    return f"{link}#{quote(node.name, safe='')}" if node.name else link


def link_matches_tested_config(node: VLESSNode) -> bool:
    """True when a client importing `node.link` gets the very outbound the scanner tests."""
    try:
        again = parse_vless(node.link)
    except ValueError:
        return False
    return build_outbound(again, "check") == build_outbound(node, "check")


# ============================================================
# XRAY STREAM SETTINGS & CONFIG BUILDER
# ============================================================

def build_stream_settings(node: VLESSNode) -> dict:
    network = normalize_network(node.network)
    if network not in {"tcp", "ws", "grpc", "xhttp", "httpupgrade"}:
        raise ValueError(f"unsupported transport: {node.network!r}")

    stream = {
        "network": "raw" if network == "tcp" else network,
        "security": node.security,
    }

    if network == "ws":
        settings = {}
        if node.path:
            settings["path"] = node.path
        if node.host:
            settings["host"] = node.host
        stream["wsSettings"] = settings

    elif network == "grpc":
        settings = {}
        if node.service_name:
            settings["serviceName"] = node.service_name
        if node.mode == "multi":
            settings["multiMode"] = True
        stream["grpcSettings"] = settings

    elif network == "xhttp":
        settings = {}
        if node.path:
            settings["path"] = node.path
        if node.host:
            settings["host"] = node.host
        if node.mode:
            settings["mode"] = node.mode
        stream["xhttpSettings"] = settings

    elif network == "httpupgrade":
        settings = {}
        if node.path:
            settings["path"] = node.path
        if node.host:
            settings["host"] = node.host
        stream["httpupgradeSettings"] = settings

    if node.security == "tls":
        settings = {}
        if node.sni:
            settings["serverName"] = node.sni
        if node.fingerprint_tls:
            settings["fingerprint"] = node.fingerprint_tls
        if node.alpn:
            settings["alpn"] = node.alpn
        if node.allow_insecure:
            settings["allowInsecure"] = node.allow_insecure
        stream["tlsSettings"] = settings

    elif node.security == "reality":
        settings = {
            "fingerprint": node.fingerprint_tls or "chrome",
        }
        if node.sni:
            settings["serverName"] = node.sni
        if node.public_key:
            settings["publicKey"] = node.public_key
        if node.short_id:
            settings["shortId"] = node.short_id
        if node.spider_x:
            settings["spiderX"] = node.spider_x
        stream["realitySettings"] = settings

    return stream


def validate_nodes_for_xray(nodes: list[VLESSNode]) -> list[VLESSNode]:
    valid = []
    for node in nodes:
        try:
            # Reset local_port before validation
            node.local_port = 0

            # Validate REALITY requirements
            if node.security == "reality":
                if not node.public_key:
                    node.success = False
                    node.error = "REALITY: missing publicKey"
                    continue
                if not node.sni:
                    node.success = False
                    node.error = "REALITY: missing SNI/serverName"
                    continue
                # An empty shortId is valid when the server lists "" among its shortIds.
                # The link then omits sid too, so the scan result is what a client gets.

            build_stream_settings(node)
            valid.append(node)
        except Exception as exc:
            node.success = False
            node.error = f"xray config: {exc}"
    return valid


def build_outbound(node: VLESSNode, tag: str) -> dict:
    """The Xray outbound for a node. Uses the real server port, never the local SOCKS port."""
    user = {
        "id": node.uuid,
        "encryption": "none",  # VLESS requires encryption="none"
        "level": 0,
    }
    if node.flow:
        user["flow"] = node.flow
    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [{
                "address": node.address,
                "port": node.port,
                "users": [user],
            }],
        },
        "streamSettings": build_stream_settings(node),
    }


def build_xray_config(nodes: list[VLESSNode]) -> dict:
    inbounds = []
    outbounds = []
    rules = []

    if DEBUG:
        # Check for duplicate nodes by address:port
        address_ports = {}
        for node in nodes:
            key = f"{node.address}:{node.port}"
            if key in address_ports:
                address_ports[key].append(node)
            else:
                address_ports[key] = [node]

        duplicates = {k: v for k, v in address_ports.items() if len(v) > 1}
        if duplicates:
            print(f"[Duplicate Check] Found {len(duplicates)} duplicate address:port combinations:")
            for addr_port, dup_nodes in list(duplicates.items())[:3]:  # Show first 3
                print(f"[Duplicate Check] {addr_port}: {len(dup_nodes)} nodes")
                for dn in dup_nodes[:2]:  # Show first 2
                    print(f"[Duplicate Check]   UUID: {dn.uuid}, Network: {dn.network}, Security: {dn.security}, Port: {dn.local_port}")

    for index, node in enumerate(nodes):
        node.local_port = config.PORT_START + index
        inbound_tag = f"scanner-in-{index}"
        outbound_tag = f"scanner-out-{index}"

        if DEBUG and index < 5:  # Show first 5 mappings
            print(f"[Port Mapping] Node {index}: {node.address}:{node.port} → Port {node.local_port} → Tag {outbound_tag}")

        inbounds.append({
            "tag": inbound_tag,
            "listen": "127.0.0.1",
            "port": node.local_port,
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": False},
        })

        outbounds.append(build_outbound(node, outbound_tag))

        rules.append({
            "type": "field",
            "inboundTag": [inbound_tag],
            "outboundTag": outbound_tag,
        })

    outbounds.extend([
        {"tag": "direct", "protocol": "freedom"},
        {"tag": "block", "protocol": "blackhole"},
    ])

    return {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": outbounds,
        "routing": {
            "domainStrategy": "AsIs",
            "rules": rules,
        },
    }


# ============================================================
# SCANNER SERVICE MANAGER
# ============================================================

@dataclass(frozen=True)
class ScanQuery:
    """What a scan looks for. Every query runs the same pipeline."""

    country: Optional[str] = None  # ISO 3166 alpha-2 of the exit country

    @property
    def key(self) -> str:
        return self.country or "all"


class ScanFailed(RuntimeError):
    """A scan could not produce results (no sources, no nodes, Xray missing...)."""


class VLESSScannerManager:
    """Runs one scan at a time and exposes live progress. Persistence lives in ScanService."""

    def __init__(self):
        self.lock = asyncio.Lock()  # one scan at a time: Xray ports and bandwidth are shared
        self.query: Optional[ScanQuery] = None
        self.stage = "idle"
        self.progress = 0.0
        self.total_candidates = 0
        self.processed_candidates = 0
        self.working_candidates = 0
        self.deep_checked = 0
        self.started_at: Optional[float] = None
        self.direct_ip: Optional[str] = None

    def ensure_xray_binary(self) -> str:
        candidates = [
            config.XRAY_BINARY,
            shutil.which("xray"),
            "/opt/homebrew/bin/xray",
            "/usr/local/bin/xray",
            "/usr/bin/xray",
            "./xray",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return str(Path(candidate).resolve())

        # Attempt auto-download Xray for current system
        if DEBUG:
            print("[Scanner] Xray binary not found. Attempting auto-download...")
        system = platform.system().lower()
        machine = platform.machine().lower()

        arch_map = {
            "x86_64": "64",
            "amd64": "64",
            "aarch64": "arm64-v8a",
            "arm64": "arm64-v8a",
        }
        arch = arch_map.get(machine, "64")

        if "linux" in system:
            asset = f"Xray-linux-{arch}.zip"
        elif "darwin" in system:
            asset = f"Xray-macos-{arch}.zip"
        else:
            asset = "Xray-linux-64.zip"

        url = f"https://github.com/XTLS/Xray-core/releases/download/v25.1.30/{asset}"
        try:
            temp_zip = Path(tempfile.gettempdir()) / "xray_download.zip"
            urllib.request.urlretrieve(url, temp_zip)

            target_binary = Path(config.XRAY_BINARY)
            with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
                for member in zip_ref.namelist():
                    if member.lower() in ("xray", "xray.exe"):
                        zip_ref.extract(member, target_binary.parent)
                        extracted = target_binary.parent / member
                        if member != target_binary.name:
                            extracted.rename(target_binary)
                        target_binary.chmod(0o755)
                        if DEBUG:
                            print(f"[Scanner] Xray downloaded successfully: {target_binary}")
                        return str(target_binary.resolve())
        except Exception as exc:
            if DEBUG:
                print(f"[Scanner] Failed to download Xray core: {exc}")

        raise RuntimeError("Xray binary is missing and auto-download failed.")

    def read_sources(self) -> List[str]:
        path = Path(config.CONFIG_FILE)
        if not path.exists():
            return []
        sources = []
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
        ):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if " #" in line:
                line = line.split(" #", 1)[0].strip()
            # Accept both remote URLs and direct VLESS links
            if not line or not (line.startswith("http://") or line.startswith("https://") or line.startswith("vless://")):
                continue
            sources.append(line)
        return list(dict.fromkeys(sources))

    async def _download_source(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> tuple[str, SourceResult]:
        try:
            timeout = aiohttp.ClientTimeout(
                total=config.SOURCE_TIMEOUT,
                connect=min(config.SOURCE_TIMEOUT, 10),
                sock_read=config.SOURCE_TIMEOUT,
            )
            async with session.get(
                url,
                timeout=timeout,
                allow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 VLESS-Scanner/2.0",
                    "Accept": "*/*",
                },
            ) as response:
                if not 200 <= response.status < 300:
                    return "", SourceResult(url, False, error=f"HTTP {response.status}")
                body = await response.text(errors="ignore")
                return body, SourceResult(url, True)
        except asyncio.TimeoutError:
            return "", SourceResult(url, False, error="timeout")
        except Exception as exc:
            return "", SourceResult(url, False, error=f"{type(exc).__name__}: {exc}")

    async def _download_all_sources(
        self, sources: List[str]
    ) -> tuple[list[tuple[str, str]], list[SourceResult]]:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(
            limit=config.SOURCE_CONCURRENCY * 2,
            limit_per_host=config.SOURCE_CONCURRENCY,
            ttl_dns_cache=300,
            ssl=ssl_context,
        )
        timeout = aiohttp.ClientTimeout(total=config.SOURCE_TIMEOUT)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            sem = asyncio.Semaphore(config.SOURCE_CONCURRENCY)

            async def worker(url: str):
                async with sem:
                    body, result = await self._download_source(session, url)
                    return url, body, result

            results = await asyncio.gather(*(worker(url) for url in sources))

        bodies = []
        source_results = []
        for url, body, result in results:
            source_results.append(result)
            if result.success:
                bodies.append((url, body))
        return bodies, source_results

    @staticmethod
    async def _xray_accepts(xray_binary: str, nodes: list[VLESSNode]) -> tuple[bool, str]:
        """Validate a config with `xray run -test` without opening any ports."""
        fd, path = tempfile.mkstemp(prefix="vless-scanner-test-", suffix=".json")
        os.close(fd)
        try:
            Path(path).write_text(json.dumps(build_xray_config(nodes)), encoding="utf-8")
            process = await asyncio.create_subprocess_exec(
                xray_binary, "run", "-test", "-config", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await process.communicate()
            lines = [ln for ln in output.decode(errors="ignore").splitlines() if "Failed" in ln]
            return process.returncode == 0, (lines[-1] if lines else "")
        finally:
            Path(path).unlink(missing_ok=True)

    async def _drop_rejected_nodes(self, xray_binary: str, nodes: list[VLESSNode]) -> list[VLESSNode]:
        """Bisect a batch that Xray refused to load and keep only the nodes it accepts."""
        ok, reason = await self._xray_accepts(xray_binary, nodes)
        if ok:
            return nodes
        if len(nodes) == 1:
            node = nodes[0]
            node.success = False
            node.error = f"xray rejected config: {reason.split('>')[-1].strip()[:160]}"
            logger.info("Xray rejected node %s:%s — %s", node.address, node.port, node.error)
            return []
        middle = len(nodes) // 2
        left = await self._drop_rejected_nodes(xray_binary, nodes[:middle])
        right = await self._drop_rejected_nodes(xray_binary, nodes[middle:])
        return left + right

    # --- Xray session ----------------------------------------------------------------

    @asynccontextmanager
    async def xray_session(self, xray_binary: str, nodes: list[VLESSNode]):
        """Run one Xray process exposing a local SOCKS port per node; yield the usable nodes.

        If Xray refuses the config, the offending nodes are isolated by bisection and
        the rest still get tested.
        """
        compatible = validate_nodes_for_xray(nodes)
        process = None
        path = None
        for attempt in range(2):
            if not compatible:
                break
            fd, path = tempfile.mkstemp(prefix="vless-scanner-", suffix=".json")
            os.close(fd)
            Path(path).write_text(json.dumps(build_xray_config(compatible)), encoding="utf-8")
            process = await asyncio.create_subprocess_exec(
                xray_binary, "run", "-config", path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.sleep(1.0)
            if process.returncode is None:
                break
            _, stderr = await process.communicate()
            Path(path).unlink(missing_ok=True)
            process = None
            reason = stderr.decode(errors="ignore").strip().splitlines()[-1:] or ["no output"]
            if attempt == 1:
                raise XrayStartError(f"Xray failed to start: {reason[0]}")
            logger.warning("Xray refused a batch of %d nodes, isolating bad nodes", len(compatible))
            compatible = await self._drop_rejected_nodes(xray_binary, compatible)

        # Drain stderr so a chatty Xray never blocks on a full pipe.
        drain = asyncio.create_task(process.stderr.read()) if process else None
        try:
            yield compatible if process else []
        finally:
            if process and process.returncode is None:
                process.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            if drain:
                drain.cancel()
            if path:
                Path(path).unlink(missing_ok=True)

    @staticmethod
    async def _for_each(nodes: list[VLESSNode], worker, concurrency: int, stop=lambda: False) -> None:
        """Run `worker(node)` over nodes with bounded concurrency until `stop()` is true."""
        queue = list(reversed(nodes))

        async def run():
            while queue and not stop():
                await worker(queue.pop())

        await asyncio.gather(*(run() for _ in range(max(1, min(concurrency, len(nodes))))))

    # --- pipeline stage 1: find genuinely working candidates -----------------------

    async def _qualify(self, node: VLESSNode, query: ScanQuery) -> bool:
        port = node.local_port
        result = await probes.trace(port, self.direct_ip)
        if not result.ok:
            node.error = result.error
            return False
        node.exit_ip, node.exit_country = result.exit_ip, result.country
        if query.country and result.country != query.country:
            node.error = f"exits in {result.country or 'unknown country'}"
            return False
        if not await probes.reach(port):
            node.error = "second destination unreachable"
            return False
        if await probes.download(port, config.QUALIFY_DOWNLOAD_BYTES, config.QUALIFY_DOWNLOAD_TIMEOUT) is None:
            node.error = "stalls during data transfer"
            return False
        node.latency_ms = result.latency_ms
        node.success = True
        node.error = ""
        return True

    async def _find_candidates(self, xray_binary: str, nodes: list[VLESSNode], query: ScanQuery) -> list[VLESSNode]:
        found: list[VLESSNode] = []
        enough = lambda: len(found) >= config.CANDIDATE_COUNT  # noqa: E731

        async def check(node: VLESSNode):
            if await self._qualify(node, query) and not enough():
                found.append(node)
                self.working_candidates = len(found)
            self.processed_candidates += 1
            self._update_progress()

        for start in range(0, len(nodes), config.BATCH_SIZE):
            if enough():
                break
            batch = nodes[start: start + config.BATCH_SIZE]
            try:
                async with self.xray_session(xray_binary, batch) as usable:
                    self.processed_candidates += len(batch) - len(usable)
                    await self._for_each(usable, check, config.WORKERS, stop=enough)
            except XrayStartError:
                logger.exception("Skipping a batch of %d nodes", len(batch))
                self.processed_candidates += len(batch)
        return found

    # --- pipeline stage 2: deep checks on the candidates ---------------------------

    async def _deep_check(self, xray_binary: str, candidates: list[VLESSNode]) -> None:
        async with self.xray_session(xray_binary, candidates) as usable:

            async def latency(node: VLESSNode):
                samples = []
                for _ in range(config.DEEP_ATTEMPTS):
                    result = await probes.trace(node.local_port, self.direct_ip)
                    if result.ok:
                        samples.append(result.latency_ms)
                    await asyncio.sleep(0.3)
                node.attempts = config.DEEP_ATTEMPTS
                node.latency_samples = samples
                node.stable = len(samples) == config.DEEP_ATTEMPTS
                node.latency_ms = median(samples) if samples else None
                node.jitter_ms = pstdev(samples) if len(samples) > 1 else None

            async def speed(node: VLESSNode):
                node.speed_mbps = await probes.download(
                    node.local_port, config.DEEP_DOWNLOAD_BYTES, config.DEEP_DOWNLOAD_TIMEOUT
                )
                self.deep_checked += 1
                self._update_progress()

            # Latency first, all at once (tiny requests); then throughput, a few at a
            # time so the downloads don't compete for our own bandwidth.
            await self._for_each(usable, latency, len(usable))
            await self._for_each(usable, speed, config.DEEP_DOWNLOAD_CONCURRENCY)

    # --- the pipeline ------------------------------------------------------------------

    def _update_progress(self) -> None:
        if self.stage == "testing":
            scanned = self.processed_candidates / max(self.total_candidates, 1)
            found = self.working_candidates / config.CANDIDATE_COUNT
            self.progress = round(min(max(scanned, found), 1) * 85, 1)
        elif self.stage == "rechecking":
            self.progress = round(85 + self.deep_checked / max(self.working_candidates, 1) * 15, 1)

    async def run_scan(self, query: ScanQuery = ScanQuery()) -> dict:
        """all sources → first CANDIDATE_COUNT working → deep checks → TOP_COUNT best.

        Scans are serialized: a second caller waits for the lock. Raises ScanFailed.
        """
        async with self.lock:
            self.query = query
            self.stage = "downloading"
            self.progress = 0.0
            self.total_candidates = self.processed_candidates = 0
            self.working_candidates = self.deep_checked = 0
            self.started_at = time.time()
            try:
                return await self._pipeline(query)
            except ScanFailed:
                self.stage = "failed"
                raise
            except Exception as exc:
                self.stage = "failed"
                logger.exception("Scan crashed")
                raise ScanFailed(f"{type(exc).__name__}: {exc}") from exc
            finally:
                self.query = None

    async def _collect(self) -> tuple[list[VLESSNode], list[SourceResult]]:
        """Download every source and return unique, Xray-compatible nodes."""
        sources = self.read_sources()
        if not sources:
            raise ScanFailed("no sources configured")
        direct = [s for s in sources if s.startswith("vless://")]
        downloaded, source_results = await self._download_all_sources([s for s in sources if s not in direct])
        source_results += [SourceResult(link, True, 1) for link in direct]

        self.stage = "parsing"
        per_source: list[list[str]] = [direct] + [extract_source_nodes(body) for _, body in downloaded]
        counts = {url: len(urls) for (url, _), urls in zip(downloaded, per_source[1:])}
        for result in source_results:
            result.vless_count = counts.get(result.url, result.vless_count)

        seen: set[str] = set()
        nodes: list[VLESSNode] = []
        for index, urls in enumerate(per_source):
            for url in urls:
                try:
                    node = parse_vless(url)
                except Exception:
                    continue
                if not link_matches_tested_config(node):
                    logger.warning("Skipping %s:%s: its share link would not reproduce the tested config",
                                   node.address, node.port)
                    continue
                if node.fingerprint not in seen:
                    seen.add(node.fingerprint)
                    node.source_index = index
                    nodes.append(node)
        return nodes[: config.MAX_NODES], source_results

    async def _pipeline(self, query: ScanQuery) -> dict:
        start_time = time.time()
        try:
            xray_binary = self.ensure_xray_binary()
        except RuntimeError as exc:
            raise ScanFailed(str(exc)) from exc

        self.direct_ip = await probes.direct_exit_ip()
        if not self.direct_ip:
            raise ScanFailed("cannot determine the scanner's own IP (is the network up?)")

        nodes, source_results = await self._collect()
        if not nodes:
            raise ScanFailed("no valid VLESS nodes extracted from sources")
        ordered = order_candidates(nodes, query)
        self.total_candidates = len(ordered)
        logger.info("[%s] %d unique nodes from %d sources", query.key, len(nodes), len(source_results))

        self.stage = "testing"
        candidates = await self._find_candidates(xray_binary, ordered, query)
        logger.info("[%s] %d candidates after checking %d nodes", query.key, len(candidates), self.processed_candidates)

        self.stage = "rechecking"
        if candidates:
            await self._deep_check(xray_binary, candidates)
        top = rank_nodes(candidates)[: config.TOP_COUNT]

        duration = round(time.time() - start_time, 2)
        self.stage = "completed"
        self.progress = 100.0
        logger.info("[%s] scan finished in %.1fs: best %d of %d candidates", query.key, duration, len(top), len(candidates))
        return {
            "country": query.country,
            "duration_seconds": duration,
            "total_nodes": len(nodes),
            "tested": self.processed_candidates,
            "working": len(candidates),
            "sources": [asdict(r) for r in source_results],
            "top": [node_result(node) for node in top],
        }

    def get_status(self) -> dict:
        return {
            "is_scanning": self.lock.locked(),
            "query": self.query.key if self.query else None,
            "stage": self.stage,
            "progress": self.progress,
            "total_candidates": self.total_candidates,
            "processed_candidates": self.processed_candidates,
            "working_candidates": self.working_candidates,
            "target_candidates": config.CANDIDATE_COUNT,
            "elapsed_seconds": round(time.time() - self.started_at, 1)
            if self.started_at and self.lock.locked() else None,
        }


def node_result(node: VLESSNode) -> dict:
    """What the cache keeps. The local SOCKS port only meant something inside this scan."""
    result = asdict(node)
    del result["local_port"]
    return result


def order_candidates(nodes: list[VLESSNode], query: ScanQuery) -> list[VLESSNode]:
    """Interleave sources so one big list can't crowd out the others; for a country
    search, try nodes whose label claims that country first (the exit is still measured)."""
    by_source: dict[int, list[VLESSNode]] = {}
    for node in nodes:
        by_source.setdefault(node.source_index, []).append(node)
    interleaved = [n for group in zip_longest(*by_source.values()) for n in group if n is not None]
    if query.country:
        interleaved.sort(key=lambda n: n.label_country != query.country)  # stable sort
    return interleaved


def rank_score(node: VLESSNode) -> float:
    """Expected cost in ms: latency penalised by failed checks, plus the time to fetch 1 MB."""
    success_ratio = len(node.latency_samples) / max(node.attempts, 1)
    return node.latency_ms * (1 + 2 * (1 - success_ratio)) + 8000 / node.speed_mbps


def is_usable(node: VLESSNode) -> bool:
    """Deep-check bar: most latency checks passed and a multi-MB download completed."""
    return (
        len(node.latency_samples) >= config.DEEP_MIN_SUCCESS
        and node.latency_ms is not None
        and bool(node.speed_mbps)
    )


def rank_nodes(candidates: list[VLESSNode]) -> list[VLESSNode]:
    return sorted((n for n in candidates if is_usable(n)), key=rank_score)


scanner_manager = VLESSScannerManager()
