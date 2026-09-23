#!/usr/bin/env python3

import asyncio
import base64
import binascii
import hashlib
import json
import re
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import Optional
from urllib.parse import parse_qs, unquote, urlsplit

import aiohttp
import certifi
from aiohttp_socks import ProxyConnector


# ============================================================
# CONFIGURATION
# ============================================================

CONFIG_FILE = "config.txt"
XRAY_BINARY = "xray"

# Number of nodes tested concurrently inside one Xray instance.
# 100-150 is usually a good balance on a desktop connection.
WORKERS = 100
BATCH_SIZE = 100

TIMEOUT = 8.0
RETRIES = 1
TOP_COUNT = 5
RECHECK_COUNT = 25
RECHECK_ATTEMPTS = 3

TEST_URL = "https://www.cloudflare.com/cdn-cgi/trace"
MAX_NODES = 5000
SOURCE_CONCURRENCY = 20
PORT_START = 20000
VERIFY_TEST_SSL = True
RESPONSE_READ_BYTES = 4096

TOP_FILE = "top5.txt"
RESULTS_FILE = "results.json"

# Keep false unless debugging Xray itself.
XRAY_VERBOSE = False

# Xray currently accepts these transports for the config generated here.
SUPPORTED_NETWORKS = {
    "tcp",
    "raw",
    "ws",
    "websocket",
    "grpc",
    "xhttp",
    "splithttp",
    "httpupgrade",
    "http-upgrade",
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


# ============================================================
# VLESS EXTRACTION
# ============================================================

# IMPORTANT:
# The lazy part + vless:// lookahead prevents this common broken source:
#   ...?type=tcpvless://UUID@host:443?...
# from becoming one giant URL. It produces two independent candidates.
VLESS_RE = re.compile(
    r"vless://[^\s\"'<>]*?(?=vless://|[\s\"'<>]|$)",
    re.IGNORECASE,
)


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class VLESSNode:
    original: str
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
    params: dict = field(default_factory=dict)
    local_port: int = 0
    latency_samples: list[float] = field(default_factory=list)
    latency_ms: Optional[float] = None
    status: Optional[int] = None
    success: bool = False
    stable: bool = False
    error: str = ""
    attempts: int = 0


@dataclass
class SourceResult:
    url: str
    success: bool
    vless_count: int = 0
    error: str = ""


# ============================================================
# HELPERS
# ============================================================

def die(message: str):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


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
            # parse_qs already URL-decodes query values once.
            # Do not blindly unquote a second time: it can turn a literal
            # %23 into # and corrupt malformed subscription data.
            return str(value)
    return default


def bool_value(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_network(value: str) -> str:
    """Return a safe canonical Xray transport name.

    Public subscription files frequently contain broken concatenations such as
    `type=tcpvless://...`. The prefix before the second vless:// is the only
    meaningful transport in that case.
    """
    value = str(value or "").strip().lower()
    value = unquote(value).strip().lower()

    # Remove accidental URL concatenation at the end of a transport value.
    if "vless://" in value:
        prefix = value.split("vless://", 1)[0].strip()
        if prefix in NETWORK_ALIASES:
            value = prefix
        else:
            value = ""

    # Fragment garbage occasionally gets placed into query values.
    if "#" in value:
        value = value.split("#", 1)[0].strip()

    # Obvious textual garbage after a valid transport.
    for separator in ("@", "&", "?"):
        if separator in value:
            value = value.split(separator, 1)[0].strip()

    return NETWORK_ALIASES.get(value, value)


def find_xray() -> str:
    candidates = [
        shutil.which(XRAY_BINARY),
        "/opt/homebrew/bin/xray",
        "/usr/local/bin/xray",
        "/usr/bin/xray",
        "./xray",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    die("Xray not found. Install Xray or change XRAY_BINARY.")
    return ""


# ============================================================
# CONFIG.TXT
# ============================================================

def read_config() -> list[str]:
    path = Path(CONFIG_FILE)
    if not path.exists():
        die(f"{CONFIG_FILE} not found. Create it with one subscription URL per line.")

    sources = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if " #" in line:
            line = line.split(" #", 1)[0].strip()
        if not line:
            continue
        if not (line.startswith("http://") or line.startswith("https://")):
            print(f"WARNING: ignoring invalid line {line_number}: {raw_line}")
            continue
        sources.append(line)

    sources = list(dict.fromkeys(sources))
    if not sources:
        die(f"{CONFIG_FILE} does not contain any URLs.")
    return sources


# ============================================================
# SUBSCRIPTION DOWNLOAD
# ============================================================

async def download_source(
    session: aiohttp.ClientSession,
    url: str,
) -> tuple[str, SourceResult]:
    try:
        timeout = aiohttp.ClientTimeout(
            total=TIMEOUT,
            connect=min(TIMEOUT, 5),
            sock_connect=min(TIMEOUT, 5),
            sock_read=TIMEOUT,
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


async def download_sources(
    sources: list[str],
) -> tuple[list[tuple[str, str]], list[SourceResult]]:
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(
        limit=SOURCE_CONCURRENCY * 2,
        limit_per_host=SOURCE_CONCURRENCY,
        ttl_dns_cache=300,
        ssl=ssl_context,
    )
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
    ) as session:
        sem = asyncio.Semaphore(SOURCE_CONCURRENCY)

        async def worker(url: str):
            async with sem:
                body, result = await download_source(session, url)
                return url, body, result

        results = await asyncio.gather(*(worker(url) for url in sources))

    bodies = []
    source_results = []
    for url, body, result in results:
        source_results.append(result)
        if result.success:
            bodies.append((url, body))
    return bodies, source_results


# ============================================================
# SUBSCRIPTION DECODING / EXTRACTION
# ============================================================

def decode_base64(text: str) -> Optional[str]:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return None

    candidates = [compact, compact.replace("-", "+").replace("_", "/")]
    for candidate in candidates:
        padding = "=" * (-len(candidate) % 4)
        try:
            decoded = base64.b64decode(candidate + padding, validate=False)
        except (binascii.Error, ValueError):
            continue
        result = decoded.decode("utf-8", errors="ignore")
        if "vless://" in result.lower():
            return result
    return None


def clean_vless_candidate(value: str) -> str:
    value = value.strip()
    value = value.rstrip(" \t\r\n.,;)]}\"'")

    # Remove control characters without touching Unicode node names.
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)

    return value


def extract_vless(text: str) -> list[str]:
    if not text:
        return []

    result = []
    for match in VLESS_RE.finditer(text):
        value = clean_vless_candidate(match.group(0))
        if not value.lower().startswith("vless://"):
            continue
        result.append(value)
    return result


def extract_source_nodes(text: str) -> list[str]:
    direct = extract_vless(text)
    if direct:
        return direct

    decoded = decode_base64(text)
    if decoded:
        return extract_vless(decoded)

    return []


# ============================================================
# VLESS PARSING
# ============================================================

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
    network = normalize_network(raw_network)
    if not network:
        network = "tcp"
    if network not in {"tcp", "ws", "grpc", "xhttp", "httpupgrade"}:
        raise ValueError(f"unsupported transport: {raw_network!r}")

    security = q(params, "security", default="none").strip().lower()
    if security in {"", "false", "0", "off", "none"}:
        security = "none"
    elif security not in {"tls", "reality"}:
        security = "none"

    encryption = q(params, "encryption", default="none").strip() or "none"
    flow = q(params, "flow").strip()

    sni = q(params, "sni", "servername", "serverName").strip()
    fingerprint_tls = q(params, "fp", "fingerprint").strip()

    alpn_raw = q(params, "alpn")
    alpn = [x.strip() for x in alpn_raw.split(",") if x.strip()]

    allow_insecure = bool_value(
        q(params, "allowInsecure", "allowinsecure", default="false")
    )

    public_key = q(params, "pbk", "publicKey").strip()
    short_id = q(params, "sid", "shortId").strip()
    spider_x = q(params, "spx", "spiderX")
    path = q(params, "path")
    host = q(params, "host")
    service_name = q(params, "serviceName")
    name = clean_name(parts.fragment)

    identity = {
        "uuid": uuid,
        "address": parts.hostname.lower(),
        "port": parts.port,
        "network": network,
        "security": security,
        "encryption": encryption,
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
    }

    fingerprint = short_hash(
        json.dumps(identity, sort_keys=True, ensure_ascii=False)
    )

    return VLESSNode(
        original=url,
        fingerprint=fingerprint,
        uuid=uuid,
        address=parts.hostname,
        port=parts.port,
        name=name,
        encryption=encryption,
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
        params=params,
    )


def parse_all_nodes(
    downloaded: list[tuple[str, str]],
) -> tuple[list[VLESSNode], int]:
    all_urls: list[str] = []

    for source_url, body in downloaded:
        urls = extract_source_nodes(body)
        print(f"  {len(urls):>5} VLESS  {source_url}")
        all_urls.extend(urls)

    seen = set()
    nodes: list[VLESSNode] = []
    invalid = 0

    for url in all_urls:
        if len(nodes) >= MAX_NODES:
            break
        try:
            node = parse_vless(url)
        except Exception:
            invalid += 1
            continue

        if node.fingerprint in seen:
            continue
        seen.add(node.fingerprint)
        nodes.append(node)

    return nodes, invalid


# ============================================================
# XRAY STREAM SETTINGS
# ============================================================

def build_stream_settings(node: VLESSNode) -> dict:
    network = normalize_network(node.network)
    if network not in {"tcp", "ws", "grpc", "xhttp", "httpupgrade"}:
        raise ValueError(f"unsupported transport after normalization: {node.network!r}")

    stream = {
        "network": "raw" if network == "tcp" else network,
        "security": node.security,
    }

    if network == "ws":
        settings = {}
        if node.path:
            settings["path"] = node.path
        # Xray 26.x warns that headers.Host is deprecated.
        if node.host:
            settings["host"] = node.host
        stream["wsSettings"] = settings

    elif network == "grpc":
        settings = {}
        if node.service_name:
            settings["serviceName"] = node.service_name
        mode = q(node.params, "mode").strip().lower()
        if mode:
            settings["multiMode"] = mode == "multi"
        stream["grpcSettings"] = settings

    elif network == "xhttp":
        settings = {}
        if node.path:
            settings["path"] = node.path
        if node.host:
            settings["host"] = node.host
        mode = q(node.params, "mode").strip()
        if mode:
            settings["mode"] = mode
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
        stream["tlsSettings"] = settings

    elif node.security == "reality":
        settings = {
            "fingerprint": node.fingerprint_tls or "chrome",
        }
        if node.sni:
            settings["serverName"] = node.sni
        if node.public_key:
            settings["password"] = node.public_key
        if node.short_id:
            settings["shortId"] = node.short_id
        if node.spider_x:
            settings["spiderX"] = node.spider_x
        stream["realitySettings"] = settings

    return stream


def validate_nodes_for_xray(nodes: list[VLESSNode]) -> list[VLESSNode]:
    """Remove bad nodes before they can invalidate the whole Xray config."""
    valid = []
    for node in nodes:
        try:
            build_stream_settings(node)
        except Exception as exc:
            node.success = False
            node.error = f"xray config: {exc}"
            continue
        valid.append(node)
    return valid


# ============================================================
# XRAY CONFIG
# ============================================================

def build_xray_config(nodes: list[VLESSNode]) -> dict:
    inbounds = []
    outbounds = []
    rules = []

    for index, node in enumerate(nodes):
        node.local_port = PORT_START + index

        inbound_tag = f"scanner-in-{index}"
        outbound_tag = f"scanner-out-{index}"

        inbounds.append({
            "tag": inbound_tag,
            "listen": "127.0.0.1",
            "port": node.local_port,
            "protocol": "socks",
            "settings": {
                "auth": "noauth",
                "udp": False,
            },
        })

        user = {
            "id": node.uuid,
            "encryption": node.encryption or "none",
            "level": 0,
        }
        if node.flow:
            user["flow"] = node.flow

        outbounds.append({
            "tag": outbound_tag,
            "protocol": "vless",
            "settings": {
                "vnext": [{
                    "address": node.address,
                    "port": node.port,
                    "users": [user],
                }],
            },
            "streamSettings": build_stream_settings(node),
        })

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
        "log": {"loglevel": "warning" if not XRAY_VERBOSE else "info"},
        "inbounds": inbounds,
        "outbounds": outbounds,
        "routing": {
            "domainStrategy": "AsIs",
            "rules": rules,
        },
    }


# ============================================================
# XRAY PROCESS
# ============================================================

class Xray:
    def __init__(self, binary: str, nodes: list[VLESSNode]):
        self.binary = binary
        self.nodes = nodes
        self.process: Optional[asyncio.subprocess.Process] = None
        self.config_path: Optional[Path] = None

    async def start(self):
        if not self.nodes:
            raise RuntimeError("No Xray-compatible nodes in batch")

        fd, path = tempfile.mkstemp(prefix="vless-scanner-", suffix=".json")
        # mkstemp gives us an actual safe file instead of tempfile.mktemp().
        import os
        os.close(fd)
        self.config_path = Path(path)

        config = build_xray_config(self.nodes)
        self.config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        test_process = await asyncio.create_subprocess_exec(
            self.binary,
            "run",
            "-test",
            "-config",
            str(self.config_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await test_process.communicate()

        if test_process.returncode != 0:
            print("\n" + "=" * 70)
            print("XRAY CONFIG ERROR")
            print("=" * 70)
            if stdout:
                print(stdout.decode(errors="replace"))
            if stderr:
                print(stderr.decode(errors="replace"))
            print(f"Config preserved: {self.config_path}")
            print("=" * 70)
            raise RuntimeError("Xray configuration is invalid")

        self.process = await asyncio.create_subprocess_exec(
            self.binary,
            "run",
            "-config",
            str(self.config_path),
            stdout=asyncio.subprocess.PIPE if not XRAY_VERBOSE else None,
            stderr=asyncio.subprocess.PIPE if not XRAY_VERBOSE else None,
        )

        # Xray normally binds all local SOCKS ports very quickly.
        await asyncio.sleep(0.25)

        if self.process.returncode is not None:
            stdout, stderr = await self.process.communicate()
            print("\n" + "=" * 70)
            print("XRAY STARTUP ERROR")
            print("=" * 70)
            if stdout:
                print(stdout.decode(errors="replace"))
            if stderr:
                print(stderr.decode(errors="replace"))
            print(f"Config preserved: {self.config_path}")
            print("=" * 70)
            raise RuntimeError("Xray exited immediately")

    async def stop(self):
        process = self.process
        self.process = None

        if process is not None and process.returncode is None:
            try:
                process.send_signal(signal.SIGTERM)
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                try:
                    await process.wait()
                except Exception:
                    pass
            except ProcessLookupError:
                pass
            except Exception:
                pass

        # Do not leave hundreds of temporary configs on disk.
        if self.config_path:
            try:
                self.config_path.unlink(missing_ok=True)
            except Exception:
                pass
            self.config_path = None


# ============================================================
# PROXY TEST
# ============================================================

async def test_once(node: VLESSNode) -> tuple[bool, Optional[float], Optional[int], str]:
    proxy = f"socks5://127.0.0.1:{node.local_port}"
    connector = None
    start = time.perf_counter()

    try:
        connector = ProxyConnector.from_url(
            proxy,
            rdns=True,
            ssl=VERIFY_TEST_SSL,
        )
        timeout = aiohttp.ClientTimeout(
            total=TIMEOUT,
            connect=TIMEOUT,
            sock_connect=TIMEOUT,
            sock_read=TIMEOUT,
        )

        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 VLESS-Scanner/2.0",
                "Accept": "*/*",
            },
        ) as session:
            async with session.get(
                TEST_URL,
                allow_redirects=True,
                max_redirects=5,
            ) as response:
                await response.content.read(RESPONSE_READ_BYTES)
                elapsed = (time.perf_counter() - start) * 1000

                if 200 <= response.status < 400:
                    return True, elapsed, response.status, ""
                return False, elapsed, response.status, f"HTTP {response.status}"

    except asyncio.TimeoutError:
        return False, None, None, "timeout"
    except (aiohttp.ClientError, OSError) as exc:
        return False, None, None, f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        return False, None, None, f"{type(exc).__name__}: {exc}"
    finally:
        if connector:
            try:
                await connector.close()
            except Exception:
                pass


# ============================================================
# WORKER POOL
# ============================================================

async def check_node(node: VLESSNode, attempts: int) -> VLESSNode:
    samples = []
    last_error = ""

    for attempt in range(attempts):
        node.attempts += 1
        ok, latency, status, error = await test_once(node)

        if ok and latency is not None:
            samples.append(latency)
            node.status = status
        else:
            last_error = error

        if attempt + 1 < attempts:
            await asyncio.sleep(0.03)

    node.latency_samples = samples
    if samples:
        node.success = True
        node.latency_ms = median(samples)
        node.stable = len(samples) == attempts
        node.error = ""
    else:
        node.success = False
        node.stable = False
        node.latency_ms = None
        node.error = last_error or "failed"

    return node


async def check_batch(nodes: list[VLESSNode], attempts: int) -> list[VLESSNode]:
    """Bounded worker pool: at most WORKERS real proxy tests at once."""
    if not nodes:
        return []

    queue: asyncio.Queue[VLESSNode] = asyncio.Queue()
    for node in nodes:
        queue.put_nowait(node)

    completed = 0
    working = 0
    lock = asyncio.Lock()

    async def worker(worker_id: int):
        nonlocal completed, working
        while True:
            try:
                node = queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            try:
                result = await check_node(node, attempts)
                async with lock:
                    completed += 1
                    if result.success:
                        working += 1
                    print(
                        f"\rChecking {completed:>4}/{len(nodes):<4} "
                        f"working={working:<4} "
                        f"workers={min(WORKERS, len(nodes)):<3}",
                        end="",
                        flush=True,
                    )
            finally:
                queue.task_done()

    worker_count = min(WORKERS, len(nodes))
    await asyncio.gather(*(worker(i) for i in range(worker_count)))
    print()
    return nodes


# ============================================================
# RANKING / OUTPUT
# ============================================================

def working_nodes(nodes: list[VLESSNode]) -> list[VLESSNode]:
    return [n for n in nodes if n.success and n.latency_ms is not None]


def rank(nodes: list[VLESSNode]) -> list[VLESSNode]:
    return sorted(
        working_nodes(nodes),
        key=lambda n: (
            not n.stable,
            n.latency_ms,
            -len(n.latency_samples),
        ),
    )


def transport(node: VLESSNode) -> str:
    return f"{normalize_network(node.network).upper()}/{node.security.upper()}"


def save_top(nodes: list[VLESSNode]):
    Path(TOP_FILE).write_text(
        "".join(node.original + "\n" for node in nodes),
        encoding="utf-8",
    )


def save_results(
    nodes: list[VLESSNode],
    source_results: list[SourceResult],
    top: list[VLESSNode],
):
    data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sources": [asdict(result) for result in source_results],
        "total_nodes": len(nodes),
        "working": len(working_nodes(nodes)),
        "top": [asdict(node) for node in top],
        "nodes": [asdict(node) for node in nodes],
    }
    Path(RESULTS_FILE).write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def print_top(nodes: list[VLESSNode]):
    print()
    print("=" * 100)
    print("TOP FASTEST WORKING VLESS")
    print("=" * 100)

    if not nodes:
        print("No working nodes found.")
        return

    print(f"{'#':<4}{'LATENCY':<12}{'STABLE':<9}{'STATUS':<9}{'TRANSPORT':<20}SERVER")
    print("-" * 100)

    for index, node in enumerate(nodes, 1):
        latency = f"{node.latency_ms:.1f} ms" if node.latency_ms is not None else "-"
        stable = "YES" if node.stable else "NO"
        server = f"{node.address}:{node.port}"
        print(
            f"{index:<4}{latency:<12}{stable:<9}"
            f"{str(node.status or '-'): <9}{transport(node):<20}{server}"
        )

    print("-" * 100)
    for index, node in enumerate(nodes, 1):
        print(f"\n{index}. {node.latency_ms:.1f} ms")
        if node.name:
            print(f"   Name: {node.name}")
        print(f"   {node.original}")


# ============================================================
# MAIN
# ============================================================

async def scan_batch(
    binary: str,
    batch: list[VLESSNode],
    attempts: int,
) -> list[VLESSNode]:
    """Validate -> start one Xray -> run many SOCKS checks in parallel."""
    compatible = validate_nodes_for_xray(batch)

    skipped = len(batch) - len(compatible)
    if skipped:
        print(f"Skipped invalid Xray configs in batch: {skipped}")

    if not compatible:
        return []

    # Ports are assigned by build_xray_config before Xray starts.
    xray = Xray(binary=binary, nodes=compatible)
    try:
        await xray.start()
        await check_batch(compatible, attempts=attempts)
        return compatible
    finally:
        await xray.stop()


async def main():
    print()
    print("╔══════════════════════════════════════╗")
    print("║          VLESS SCANNER 2.0          ║")
    print("╚══════════════════════════════════════╝")
    print()

    sources = read_config()
    print(f"Sources in {CONFIG_FILE}: {len(sources)}")

    xray_binary = find_xray()
    print(f"Xray: {xray_binary}")
    print(f"Workers: {WORKERS}")
    print(f"Batch size: {BATCH_SIZE}")

    print("\nDownloading subscriptions...")
    downloaded, source_results = await download_sources(sources)

    for result in source_results:
        if result.success:
            print(f"[OK]   {result.url}")
        else:
            print(f"[FAIL] {result.url}")
            print(f"       {result.error}")

    print("\nExtracting VLESS links...")
    nodes, invalid = parse_all_nodes(downloaded)

    print(f"\nUnique valid nodes: {len(nodes)}")
    print(f"Invalid nodes: {invalid}")

    if not nodes:
        die("No valid VLESS nodes found.")

    all_working: list[VLESSNode] = []
    total = len(nodes)

    for batch_start in range(0, total, BATCH_SIZE):
        batch = nodes[batch_start:batch_start + BATCH_SIZE]
        end = batch_start + len(batch)

        print(f"\nBatch {batch_start + 1}-{end}/{total}")

        try:
            working = await scan_batch(
                xray_binary,
                batch,
                attempts=1 + RETRIES,
            )
            all_working.extend(working_nodes(working))
        except RuntimeError as exc:
            # One bad batch must not kill a 5000-node scan.
            for node in batch:
                node.success = False
                node.error = str(exc)
            print(f"Batch skipped: {exc}")

    print(f"\nFirst pass complete. Working nodes: {len(all_working)}")

    first_rank = rank(nodes)
    candidates = first_rank[:RECHECK_COUNT]

    if candidates:
        print(f"\nRechecking {len(candidates)} fastest candidates...")
        try:
            # Start Xray first; build_xray_config assigns the correct ports.
            compatible = validate_nodes_for_xray(candidates)
            xray = Xray(binary=xray_binary, nodes=compatible)
            try:
                await xray.start()
                await check_batch(compatible, attempts=RECHECK_ATTEMPTS)
            finally:
                await xray.stop()
        except RuntimeError as exc:
            print(f"Recheck skipped: {exc}")

    final_rank = rank(nodes)
    top = final_rank[:TOP_COUNT]

    save_top(top)
    save_results(nodes, source_results, top)
    print_top(top)

    print(f"\nSaved: {TOP_FILE}")
    print(f"Saved: {RESULTS_FILE}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(130)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
