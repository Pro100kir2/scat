import asyncio

import pytest

from app.api import rate_limit
from app.services import scan_service as scan_service_module
from app.services.scan_service import ScanService

REALITY_LINK = (
    "vless://11111111-2222-3333-4444-555555555555@example.com:443"
    "?type=tcp&security=reality&sni=www.microsoft.com&fp=chrome"
    "&pbk=Yx0lZ5bqG3N5M5xk0pWkZxQy3qvC0d5c2mV9rBzH1Qw&sid=6ba85179e30d4fc2"
    "#%F0%9F%87%A9%F0%9F%87%AA%20Frankfurt%20%40ads_channel"
)


def make_node(i: int = 0, latency: float = 120.0) -> dict:
    return {
        "link": REALITY_LINK.replace("example.com", f"n{i}.example.com"),
        "fingerprint": f"fp{i}",
        "uuid": "11111111-2222-3333-4444-555555555555",
        "address": f"n{i}.example.com",
        "port": 443,
        "name": "🇩🇪 Frankfurt @ads_channel",
        "network": "tcp",
        "security": "reality",
        "sni": "www.microsoft.com",
        "flow": "",
        "fingerprint_tls": "chrome",
        "latency_ms": latency,
        "latency_samples": [latency] * 5,
        "stable": True,
        "attempts": 5,
        "exit_country": "DE",
        "speed_mbps": 12.345,
        "jitter_ms": 8.4,
    }


class FakeScanner:
    """Stands in for the Xray scanner: counts runs and can be made slow or failing."""

    def __init__(self, nodes=None, delay: float = 0.05, fail: bool = False):
        self.runs = 0
        self.queries = []
        self.nodes = [make_node(0)] if nodes is None else nodes
        self.delay = delay
        self.fail = fail

    async def run_scan(self, query=None) -> dict:
        self.runs += 1
        self.queries.append(query)
        await asyncio.sleep(self.delay)
        if self.fail:
            raise scan_service_module.ScanFailed("boom")
        return {
            "duration_seconds": self.delay,
            "country": getattr(query, "country", None),
            "total_nodes": 100,
            "tested": 60,
            "working": 25,
            "sources": [],
            "top": self.nodes,
        }

    def get_status(self) -> dict:
        return {"query": None, "stage": "testing", "progress": 50.0}

    def read_sources(self) -> list[str]:
        return ["https://example.com/sub.txt"]


@pytest.fixture
def make_service(tmp_path):
    def factory(scanner=None, ttl: int = 86400, version: str = "test"):
        scanner = scanner or FakeScanner()
        return ScanService(scanner, tmp_path, ttl, version, tmp_path / "scan.lock", max_queued=2)

    return factory


@pytest.fixture
def api_service(make_service, monkeypatch):
    """Point the API at an isolated service with a fake scanner."""
    from app.api import routes

    service = make_service()
    monkeypatch.setattr(routes, "scan_service", service)
    monkeypatch.setattr(rate_limit.scan_limiter, "_hits", {})
    return service


# --- provider probes ------------------------------------------------------------------

class FakeClock:
    def __init__(self, start: float = 1_700_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


PROBE_PROFILES_BUILT = ("linux-amd64", "linux-arm64", "linux-armv7", "linux-mipsle", "linux-mips")


def make_dist(path, profiles=PROBE_PROFILES_BUILT, size: int = 4 * 1024 * 1024):
    """A fake probe release directory: sparse binaries with real checksums."""
    import hashlib

    path.mkdir(parents=True, exist_ok=True)
    (path / "VERSION").write_text("1.0.0\n")
    sums = []
    for profile in profiles:
        binary = path / f"scat-probe-{profile}"
        binary.write_bytes(b"\x7fELF" + profile.encode() + b"\0" * (size - 4 - len(profile)))
        sums.append(f"{hashlib.sha256(binary.read_bytes()).hexdigest()}  scat-probe-{profile}")
    (path / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    return path


@pytest.fixture(autouse=True)
def probe_service(tmp_path, monkeypatch):
    """Every test gets its own probe database, clock and release directory; the real
    DATA_DIR is never touched."""
    from app import config
    from app.api import rate_limit as rl
    from app.probes import api as probes_api, service as probe_module, targets
    from app.probes.releases import load_manifest
    from app.probes.store import Store

    dist = make_dist(tmp_path / "probe-dist")
    monkeypatch.setattr(config, "PROBE_DIST_DIR", dist)
    clock = FakeClock()
    service = probe_module.ProbeService(Store(tmp_path / "probes.db"), lambda: load_manifest(dist), clock=clock)
    service.clock_ = clock

    async def fake_control(url):
        return targets.Target("HTTP_GET", "142.250.74.35", 443, server_name="www.gstatic.com",
                              path="/generate_204", tls=True)

    monkeypatch.setattr(probe_module, "control_target", fake_control)
    monkeypatch.setattr(probe_module, "probe_service", service)
    for limiter in (probes_api.enroll_limiter, probes_api.public_limiter, probes_api.probe_limiter,
                    probes_api.auth_fail_limiter, rl.scan_limiter):
        monkeypatch.setattr(limiter, "_hits", {})
    yield service
    service.store.close()


def public_node_at(i: int, ip: str, security: str = "reality", latency: float = 120.0) -> dict:
    """A scanned config whose address is a public IP literal (no DNS in tests)."""
    node = make_node(i, latency)
    node.update(address=ip, link=node["link"].replace(f"n{i}.example.com", ip), security=security)
    if security == "none":
        node["sni"] = ""
    return node
