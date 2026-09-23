import asyncio
import base64
import binascii
import hashlib
import json
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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import Optional, Dict, Any, List
from urllib.parse import parse_qs, unquote, urlsplit

import aiohttp
import certifi
from aiohttp_socks import ProxyConnector

from app import config


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
    return value


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
    flow = q(params, "flow").strip()
    sni = q(params, "sni", "servername", "serverName").strip()
    fingerprint_tls = q(params, "fp", "fingerprint").strip()

    alpn_raw = q(params, "alpn")
    alpn = [x.strip() for x in alpn_raw.split(",") if x.strip()]

    allow_insecure = bool_value(q(params, "allowInsecure", "allowinsecure", default="false"))
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

    fingerprint = short_hash(json.dumps(identity, sort_keys=True, ensure_ascii=False))

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
            build_stream_settings(node)
            valid.append(node)
        except Exception as exc:
            node.success = False
            node.error = f"xray config: {exc}"
    return valid


def build_xray_config(nodes: list[VLESSNode]) -> dict:
    inbounds = []
    outbounds = []
    rules = []

    for index, node in enumerate(nodes):
        node.local_port = config.PORT_START + index
        inbound_tag = f"scanner-in-{index}"
        outbound_tag = f"scanner-out-{index}"

        inbounds.append({
            "tag": inbound_tag,
            "listen": "127.0.0.1",
            "port": node.local_port,
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": False},
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

class VLESSScannerManager:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.is_scanning = False
        self.stage = "idle"
        self.progress = 0.0
        self.total_candidates = 0
        self.processed_candidates = 0
        self.working_candidates = 0
        self.last_scan_time: Optional[str] = None
        self.last_duration_seconds: float = 0.0
        self.top_nodes: List[dict] = []
        self.all_nodes: List[dict] = []
        self.sources_status: List[dict] = []
        self.error_message: str = ""

        # Attempt initial load from saved file if present
        self.load_results()

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
                        print(f"[Scanner] Xray downloaded successfully: {target_binary}")
                        return str(target_binary.resolve())
        except Exception as exc:
            print(f"[Scanner] Failed to download Xray core: {exc}")

        raise RuntimeError("Xray binary is missing and auto-download failed.")

    def load_results(self):
        try:
            results_path = Path(config.RESULTS_FILE)
            if results_path.exists():
                data = json.loads(results_path.read_text(encoding="utf-8"))
                self.top_nodes = data.get("top", [])
                self.all_nodes = data.get("nodes", [])
                self.sources_status = data.get("sources", [])
                self.last_scan_time = data.get("timestamp")
                self.working_candidates = data.get("working", len(self.top_nodes))
                self.total_candidates = data.get("total_nodes", len(self.all_nodes))
                print(f"[Scanner] Loaded {len(self.top_nodes)} top nodes from {config.RESULTS_FILE}")
        except Exception as exc:
            print(f"[Scanner] Could not load saved results: {exc}")

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
            if not line or not (line.startswith("http://") or line.startswith("https://")):
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
                total=config.TIMEOUT,
                connect=min(config.TIMEOUT, 5),
                sock_read=config.TIMEOUT,
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
        timeout = aiohttp.ClientTimeout(total=config.TIMEOUT)

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

    async def _test_once(self, node: VLESSNode) -> tuple[bool, Optional[float], Optional[int], str]:
        proxy = f"socks5://127.0.0.1:{node.local_port}"
        connector = None
        start = time.perf_counter()

        try:
            connector = ProxyConnector.from_url(proxy, rdns=True, ssl=True)
            timeout = aiohttp.ClientTimeout(
                total=config.TIMEOUT,
                connect=config.TIMEOUT,
                sock_read=config.TIMEOUT,
            )

            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0 VLESS-Scanner/2.0", "Accept": "*/*"},
            ) as session:
                async with session.get(config.TEST_URL, allow_redirects=True, max_redirects=5) as resp:
                    await resp.content.read(4096)
                    elapsed = (time.perf_counter() - start) * 1000
                    if 200 <= resp.status < 400:
                        return True, elapsed, resp.status, ""
                    return False, elapsed, resp.status, f"HTTP {resp.status}"
        except asyncio.TimeoutError:
            return False, None, None, "timeout"
        except Exception as exc:
            return False, None, None, f"{type(exc).__name__}: {exc}"
        finally:
            if connector:
                try:
                    await connector.close()
                except Exception:
                    pass

    async def _check_node(self, node: VLESSNode, attempts: int) -> VLESSNode:
        samples = []
        last_error = ""

        for attempt in range(attempts):
            node.attempts += 1
            ok, latency, status, error = await self._test_once(node)

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

    async def _check_batch(self, nodes: list[VLESSNode], attempts: int) -> list[VLESSNode]:
        if not nodes:
            return []

        queue: asyncio.Queue[VLESSNode] = asyncio.Queue()
        for node in nodes:
            queue.put_nowait(node)

        lock = asyncio.Lock()

        async def worker():
            while True:
                try:
                    node = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return

                try:
                    result = await self._check_node(node, attempts)
                    async with lock:
                        self.processed_candidates += 1
                        if result.success:
                            self.working_candidates += 1
                        if self.total_candidates > 0:
                            self.progress = round((self.processed_candidates / self.total_candidates) * 100, 1)
                finally:
                    queue.task_done()

        worker_count = min(config.WORKERS, len(nodes))
        await asyncio.gather(*(worker() for _ in range(worker_count)))
        return nodes

    async def _scan_batch_with_xray(
        self, xray_binary: str, batch: list[VLESSNode], attempts: int
    ) -> list[VLESSNode]:
        compatible = validate_nodes_for_xray(batch)
        if not compatible:
            return []

        # Run Xray process for batch
        fd, path = tempfile.mkstemp(prefix="vless-scanner-", suffix=".json")
        os.close(fd)
        config_path = Path(path)

        try:
            xray_config = build_xray_config(compatible)
            config_path.write_text(json.dumps(xray_config, ensure_ascii=False, indent=2), encoding="utf-8")

            process = await asyncio.create_subprocess_exec(
                xray_binary,
                "run",
                "-config",
                str(config_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            await asyncio.sleep(0.25)

            if process.returncode is not None:
                stdout, stderr = await process.communicate()
                raise RuntimeError(f"Xray process failed to start: {stderr.decode(errors='ignore')}")

            try:
                await self._check_batch(compatible, attempts=attempts)
            finally:
                if process.returncode is None:
                    try:
                        process.send_signal(signal.SIGTERM)
                        await asyncio.wait_for(process.wait(), timeout=1.5)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass
            return compatible
        finally:
            config_path.unlink(missing_ok=True)

    async def run_scan(self) -> dict:
        if self.lock.locked():
            return {"status": "error", "message": "Scan already in progress"}

        async with self.lock:
            start_time = time.time()
            self.is_scanning = True
            self.error_message = ""
            self.stage = "downloading"
            self.progress = 0.0
            self.total_candidates = 0
            self.processed_candidates = 0
            self.working_candidates = 0

            try:
                xray_binary = self.ensure_xray_binary()

                sources = self.read_sources()
                if not sources:
                    raise RuntimeError("No sources found in config.txt")

                downloaded, source_results = await self._download_all_sources(sources)

                self.stage = "parsing"
                all_urls = []
                for source_url, body in downloaded:
                    urls = extract_source_nodes(body)
                    all_urls.extend(urls)

                seen = set()
                nodes: list[VLESSNode] = []
                for url in all_urls:
                    if len(nodes) >= config.MAX_NODES:
                        break
                    try:
                        node = parse_vless(url)
                        if node.fingerprint not in seen:
                            seen.add(node.fingerprint)
                            nodes.append(node)
                    except Exception:
                        continue

                self.total_candidates = len(nodes)
                if not nodes:
                    raise RuntimeError("No valid VLESS nodes extracted from sources")

                self.stage = "testing"
                all_working: list[VLESSNode] = []

                for batch_start in range(0, len(nodes), config.BATCH_SIZE):
                    batch = nodes[batch_start : batch_start + config.BATCH_SIZE]
                    try:
                        working = await self._scan_batch_with_xray(
                            xray_binary, batch, attempts=1 + config.RETRIES
                        )
                        all_working.extend([n for n in working if n.success])
                    except Exception as exc:
                        print(f"[Scanner] Batch error: {exc}")

                # Rank and recheck candidates
                self.stage = "rechecking"
                ranked = sorted(
                    [n for n in nodes if n.success and n.latency_ms is not None],
                    key=lambda n: (not n.stable, n.latency_ms, -len(n.latency_samples)),
                )

                candidates = ranked[: config.RECHECK_COUNT]
                if candidates:
                    try:
                        compatible = validate_nodes_for_xray(candidates)
                        await self._scan_batch_with_xray(
                            xray_binary, compatible, attempts=config.RECHECK_ATTEMPTS
                        )
                    except Exception as exc:
                        print(f"[Scanner] Recheck error: {exc}")

                final_ranked = sorted(
                    [n for n in nodes if n.success and n.latency_ms is not None],
                    key=lambda n: (not n.stable, n.latency_ms, -len(n.latency_samples)),
                )

                top = final_ranked[: config.TOP_COUNT]

                # Save top5.txt
                Path(config.TOP_FILE).write_text(
                    "".join(node.original + "\n" for node in top),
                    encoding="utf-8",
                )

                # Save results.json
                timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                results_data = {
                    "timestamp": timestamp,
                    "sources": [asdict(r) for r in source_results],
                    "total_nodes": len(nodes),
                    "working": len(final_ranked),
                    "top": [asdict(node) for node in top],
                    "nodes": [asdict(node) for node in nodes],
                }
                Path(config.RESULTS_FILE).write_text(
                    json.dumps(results_data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

                self.top_nodes = [asdict(node) for node in top]
                self.all_nodes = [asdict(node) for node in nodes]
                self.sources_status = [asdict(r) for r in source_results]
                self.last_scan_time = timestamp
                self.last_duration_seconds = round(time.time() - start_time, 2)
                self.stage = "completed"
                self.progress = 100.0

                return {
                    "status": "success",
                    "total": len(nodes),
                    "working": len(final_ranked),
                    "top_count": len(top),
                    "duration_seconds": self.last_duration_seconds,
                }

            except Exception as exc:
                self.error_message = str(exc)
                self.stage = "failed"
                print(f"[Scanner] Scan failed: {exc}")
                return {"status": "error", "message": str(exc)}
            finally:
                self.is_scanning = False

    def get_status(self) -> dict:
        return {
            "is_scanning": self.is_scanning,
            "stage": self.stage,
            "progress": self.progress,
            "total_candidates": self.total_candidates,
            "processed_candidates": self.processed_candidates,
            "working_candidates": self.working_candidates,
            "last_scan_time": self.last_scan_time,
            "last_duration_seconds": self.last_duration_seconds,
            "error_message": self.error_message,
        }


scanner_manager = VLESSScannerManager()
