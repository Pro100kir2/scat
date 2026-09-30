"""Shared harness for the distributed probe tests: a real HTTP server, real TLS endpoints.

* The FastAPI app runs under uvicorn on 127.0.0.1 in its own thread, with the real probe
  service (its own SQLite file), the real scan service and listener, and a scanner that
  returns a fixed set of backend-verified candidates (the Xray part is not under test here).
* Each candidate's endpoint is a real TLS server on its own loopback port that counts
  completed handshakes: the proof that probes really connected.
* Loopback is allowed as a target on both sides, exactly as the Go tests allow it; nothing
  else about validation changes.
"""
import asyncio
import shutil
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from app import config
from app.probes import api as probes_api, service as probe_module, targets
from app.services import scan_history as history_module, safe_fetch

SNI = "www.microsoft.com"
ADMIN_TOKEN = "integration-admin-token-" + "x" * 20
API_HOST = "scat.test"  # the verification URL's host; resolves to 127.0.0.1 here


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_cert(directory: Path) -> tuple[Path, Path]:
    if not shutil.which("openssl"):
        pytest.skip("openssl is needed to create the test TLS certificate")
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
                    "-days", "1", "-subj", f"/CN={SNI}"], check=True, capture_output=True)
    return cert, key


class TlsEndpoints:
    """One TLS server per candidate, on its own thread's loop. `handshakes[port]` counts
    connections that completed a TLS handshake (asyncio calls back only after it)."""

    def __init__(self, cert: Path, key: Path, count: int):
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, key)
        self.ports = [free_port() for _ in range(count)]
        self.handshakes = {p: 0 for p in self.ports}
        self.control_port = free_port()  # the probes' own-internet check: plain HTTP 204
        self.control_hits = 0
        self.loop = asyncio.new_event_loop()
        self._servers = []
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)

    def start(self):
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._start(), self.loop).result(10)
        return self

    async def _start(self):
        for port in self.ports:
            async def on_connect(reader, writer, port=port):
                self.handshakes[port] += 1
                writer.close()
            self._servers.append(await asyncio.start_server(on_connect, "127.0.0.1", port, ssl=self.ctx))

        async def control(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            self.control_hits += 1
            writer.write(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
        self._servers.append(await asyncio.start_server(control, "127.0.0.1", self.control_port))

    def stop(self):
        async def close():
            for server in self._servers:
                server.close()
        asyncio.run_coroutine_threadsafe(close(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)


class ApiServer:
    def __init__(self, app):
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning",
                                                    lifespan="on", timeout_keep_alive=30))
        self._thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        self._thread.start()
        deadline = time.time() + 15
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("API server did not start")
            time.sleep(0.05)
        return self

    def stop(self):
        self.server.should_exit = True
        self._thread.join(15)


def candidate(name: str, port: int, latency: float) -> dict:
    """A config the backend found usable (5/5 latency checks, a completed download)."""
    return {
        "link": f"vless://11111111-2222-3333-4444-555555555555@127.0.0.1:{port}?type=tcp&security=reality"
                f"&sni={SNI}&fp=chrome&pbk=Yx0lZ5bqG3N5M5xk0pWkZxQy3qvC0d5c2mV9rBzH1Qw&sid=6ba85179#{name}",
        "fingerprint": f"fp-{name}", "uuid": "11111111-2222-3333-4444-555555555555", "address": "127.0.0.1",
        "port": port, "name": name, "network": "tcp", "security": "reality", "sni": SNI, "flow": "",
        "fingerprint_tls": "chrome", "latency_ms": latency, "latency_samples": [latency] * 5, "stable": True,
        "attempts": 5, "exit_country": "DE", "speed_mbps": 50.0, "jitter_ms": 1.0,
    }


class FixedScanner:
    """The scan result the distributed part starts from: candidates in the backend's order."""

    def __init__(self, nodes: list[dict]):
        self.nodes = nodes
        self.runs = 0

    async def run_scan(self, query=None) -> dict:
        self.runs += 1
        await asyncio.sleep(0.05)
        return {"country": None, "duration_seconds": 0.05, "total_nodes": len(self.nodes), "tested": len(self.nodes),
                "working": len(self.nodes), "sources": [], "top": self.nodes[: config.TOP_COUNT], "ranked": self.nodes}

    def get_status(self) -> dict:
        return {"query": None, "stage": "idle", "progress": 0.0}

    def read_sources(self) -> list[str]:
        return []


def loopback_or_public(ip: str) -> bool:
    return ip == "127.0.0.1" or ORIGINAL_IS_PUBLIC(ip)


ORIGINAL_IS_PUBLIC = targets.is_public_ip


@pytest.fixture
def distributed(monkeypatch, tmp_path, probe_service, make_service):
    """Real server + probe service on the real clock + TLS endpoints. Yields a dict of parts."""
    from app.main import app, schedule_probe_round
    from app.api import routes
    from app.services import scan_service as scan_service_module

    probe_service.clock = time.time
    monkeypatch.setattr(config, "ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setattr(config, "AUTO_REFRESH", False)
    monkeypatch.setattr(config, "AUTO_SCAN_ON_STARTUP", False)
    monkeypatch.setattr(config, "PROBE_HEARTBEAT_SECONDS", 5)
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", True)  # as behind Caddy: X-Forwarded-For is the probe's IP
    monkeypatch.setattr(config, "PROBE_SPOT_CHECK_SECONDS", 1)  # every round carries a hidden check
    monkeypatch.setattr(probes_api.enroll_limiter, "limit", 10_000)
    monkeypatch.setattr(probes_api.public_limiter, "limit", 10_000)
    monkeypatch.setattr(targets, "is_public_ip", loopback_or_public)
    monkeypatch.setattr(safe_fetch, "address_allowed", loopback_or_public)

    async def resolve(host, timeout=5.0):
        assert host == API_HOST
        return "127.0.0.1"

    monkeypatch.setattr(probe_module, "resolve_public", resolve)
    cert, key = make_cert(tmp_path)
    endpoints = TlsEndpoints(cert, key, 8).start()

    async def control(url):
        return targets.Target("HTTP_GET", "127.0.0.1", endpoints.control_port, server_name="control.test",
                              path="/generate_204", tls=False)

    monkeypatch.setattr(probe_module, "control_target", control)
    names = "HGFEDCBA"  # backend order: H is the fastest, A the slowest
    nodes = [candidate(n, endpoints.ports["ABCDEFGH".index(n)], 50 + 10 * i) for i, n in enumerate(names)]
    scanner = FixedScanner(nodes)
    service = make_service(scanner)
    service.add_listener(schedule_probe_round)
    service.history = history_module.scan_history
    monkeypatch.setattr(routes, "scan_service", service)
    monkeypatch.setattr(scan_service_module, "scan_service", service)

    server = ApiServer(app)
    monkeypatch.setattr(config, "PUBLIC_API_URL", f"http://{API_HOST}:{server.port}")
    server.start()
    try:
        yield {"server": server, "endpoints": endpoints, "service": service, "scanner": scanner,
               "port_of": {n: endpoints.ports["ABCDEFGH".index(n)] for n in "ABCDEFGH"},
               "admin": {"Authorization": f"Bearer {ADMIN_TOKEN}"}}
    finally:
        server.stop()
        endpoints.stop()


# Which of the four providers' networks let each candidate through: the scenario of the
# task (A 4/4, B 1/4, C 3/4, D 4/4, E 2/4, F 4/4, G 3/4, H 1/4).
PROVIDERS = ["МТС", "Билайн", "МегаФон", "T2"]
REACHES = {
    "A": {"МТС", "Билайн", "МегаФон", "T2"},
    "B": {"МТС"},
    "C": {"МТС", "Билайн", "МегаФон"},
    "D": {"МТС", "Билайн", "МегаФон", "T2"},
    "E": {"Билайн", "МегаФон"},
    "F": {"МТС", "Билайн", "МегаФон", "T2"},
    "G": {"Билайн", "МегаФон", "T2"},
    "H": {"T2"},
}
EXPECTED_SCORES = {name: len(providers) for name, providers in REACHES.items()}
# Score first, backend order second: F(3rd) D(5th) A(8th) have 4; G(2nd) C(6th) have 3.
EXPECTED_TOP5 = ["F", "D", "A", "G", "C"]
# 25 probes: 7 + 6 + 6 + 6.
PROBES_PER_PROVIDER = {"МТС": 7, "Билайн": 6, "МегаФон": 6, "T2": 6}
