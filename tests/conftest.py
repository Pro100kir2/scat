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
        "original": REALITY_LINK.replace("example.com", f"n{i}.example.com"),
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
