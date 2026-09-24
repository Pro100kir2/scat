import asyncio
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app import config
from app.services import probes
from app.services import scanner as scanner_module
from app.services.presenter import clean_label
from app.services.scanner import (
    ScanQuery,
    VLESSScannerManager,
    build_xray_config,
    country_from_name,
    order_candidates,
    parse_vless,
)
from tests.conftest import REALITY_LINK

# --- parsing ----------------------------------------------------------------------


def test_parse_reality_link():
    node = parse_vless(REALITY_LINK)
    assert node.address == "example.com"
    assert node.security == "reality"
    assert node.fingerprint_tls == "chrome"
    assert node.short_id == "6ba85179e30d4fc2"
    assert node.label_country == "DE"


def test_unknown_tls_fingerprint_is_dropped():
    node = parse_vless(REALITY_LINK.replace("fp=chrome", "fp=hellochrome_120"))
    assert node.fingerprint_tls == ""
    reality = build_xray_config([node])["outbounds"][0]["streamSettings"]["realitySettings"]
    assert reality["fingerprint"] == "chrome"


@pytest.mark.parametrize(
    "replacement",
    [
        ("sid=6ba85179e30d4fc2", "sid=zz"),  # not hex
        ("sid=6ba85179e30d4fc2", "sid=6ba85179e30d4fc2aa"),  # too long
        ("pbk=Yx0lZ5bqG3N5M5xk0pWkZxQy3qvC0d5c2mV9rBzH1Qw", "pbk=short"),
        ("type=tcp", "type=tcp&flow=xtls-rprx-direct"),  # removed from Xray
    ],
)
def test_links_xray_would_reject_are_skipped(replacement):
    with pytest.raises(ValueError):
        parse_vless(REALITY_LINK.replace(*replacement))


def test_junk_alpn_is_filtered():
    link = REALITY_LINK.replace("type=tcp", "type=tcp&alpn=h2,v2rayNplus--junk,http/1.1")
    assert parse_vless(link).alpn == ["h2", "http/1.1"]


def test_country_and_label():
    assert country_from_name("🇳🇱 Amsterdam") == "NL"
    assert country_from_name("no flag") is None
    assert clean_label("🇩🇪 Frankfurt @ads_channel https://t.me/x") == "Frankfurt"


@pytest.mark.skipif(
    not (Path(config.XRAY_BINARY).exists() or shutil.which("xray")),
    reason="xray binary not installed",
)
def test_bisection_drops_only_the_node_xray_rejects():
    manager = VLESSScannerManager()
    good = [parse_vless(REALITY_LINK.replace("example.com", f"n{i}.example.com")) for i in range(5)]
    bad = parse_vless(REALITY_LINK.replace("example.com", "bad.example.com"))
    bad.fingerprint_tls = "hellochrome_120"  # bypasses parser sanitizing on purpose
    nodes = good[:2] + [bad] + good[2:]

    accepted = asyncio.run(manager._drop_rejected_nodes(manager.ensure_xray_binary(), nodes))
    assert bad not in accepted
    assert len(accepted) == 5
    assert bad.error.startswith("xray rejected config")


# --- the pipeline, with a simulated network ---------------------------------------

DIRECT_IP = "203.0.113.1"


def make_nodes(kinds: list[str], source_index: int = 0, label: str = "DE"):
    nodes = []
    for i, kind in enumerate(kinds):
        link = REALITY_LINK.replace("example.com", f"{kind}-{source_index}-{i}.example.com")
        link = link.split("#")[0] + ("#%F0%9F%87%AB%F0%9F%87%B7" if label == "FR" else "#%F0%9F%87%A9%F0%9F%87%AA")
        node = parse_vless(link)
        node.source_index = source_index
        nodes.append(node)
    return nodes


class Network:
    """Fake network behind probes.fetch; behaviour is picked from the node's host name.

    good  – works everywhere          fast  – works, lower latency
    tls   – TLS interception          captive – answers 200 with a login page
    leak  – exits with our own IP     noreach – only the trace host works
    stall – stalls during downloads   fr    – works, but exits in France
    flaky – passes qualification, then fails 2 of 5 deep checks
    slow  – works, but downloads slowly
    """

    def __init__(self):
        self.by_port: dict[int, str] = {}
        self.trace_calls: dict[int, int] = {}

    def attach(self, nodes):
        for i, node in enumerate(nodes):
            node.local_port = 30000 + i
            self.by_port[node.local_port] = node.address

    async def fetch(self, port, url, timeout, max_bytes=16 * 1024):
        if port is None:
            return probes.Fetch(200, f"h=www.cloudflare.com\nip={DIRECT_IP}\nloc=RU\n".encode(), 50)
        kind = self.by_port[port].split("-")[0]
        if url == config.TRACE_URL:
            self.trace_calls[port] = self.trace_calls.get(port, 0) + 1
            if kind == "tls":
                return probes.Fetch(error="TLS verification failed (traffic intercepted)")
            if kind == "captive":
                return probes.Fetch(200, b"<html>please log in</html>", 90)
            if kind == "flaky" and self.trace_calls[port] in (3, 5):
                return probes.Fetch(error="timeout")
            ip = DIRECT_IP if kind == "leak" else f"198.51.100.{port % 250}"
            loc = "FR" if kind == "fr" else "DE"
            latency = 120 if kind == "fast" else 400
            return probes.Fetch(200, f"h=www.cloudflare.com\nip={ip}\nloc={loc}\n".encode(), latency)
        if url == config.REACH_URL:
            return probes.Fetch(error="timeout") if kind == "noreach" else probes.Fetch(204, b"", 80)
        if url.startswith(config.SPEED_URL):
            if kind == "stall":
                return probes.Fetch(200, b"x" * 16_000, timeout * 1000)
            elapsed = 8000 if kind == "slow" else 400
            return probes.Fetch(200, b"x" * max_bytes, elapsed)
        raise AssertionError(url)


@pytest.fixture
def pipeline(monkeypatch):
    """A scanner whose Xray and network are simulated; returns (manager, network, set_nodes)."""
    network = Network()
    manager = VLESSScannerManager()
    state = {"nodes": []}

    @asynccontextmanager
    async def fake_session(_binary, nodes):
        network.attach(nodes)
        yield list(nodes)

    async def fake_collect():
        return state["nodes"], []

    monkeypatch.setattr(probes, "fetch", network.fetch)
    monkeypatch.setattr(manager, "xray_session", fake_session)
    monkeypatch.setattr(manager, "_collect", fake_collect)
    monkeypatch.setattr(manager, "ensure_xray_binary", lambda: "xray")
    monkeypatch.setattr(scanner_module.asyncio, "sleep", _no_sleep)

    def set_nodes(nodes):
        state["nodes"] = nodes

    return manager, network, set_nodes


async def _no_sleep(_delay):
    return None


def test_qualification_rejects_every_false_positive(pipeline):
    manager, network, set_nodes = pipeline
    fakes = make_nodes(["tls", "captive", "leak", "noreach", "stall"])
    set_nodes(fakes + make_nodes(["good"] * 3, source_index=1))

    result = asyncio.run(manager.run_scan())

    assert result["working"] == 3
    errors = {n.address.split("-")[0]: n.error for n in fakes}
    assert "TLS" in errors["tls"]
    assert errors["captive"] == "unexpected trace content"
    assert errors["leak"].startswith("not tunnelled")
    assert errors["noreach"] == "second destination unreachable"
    assert errors["stall"] == "stalls during data transfer"
    assert not any(n.success for n in fakes)


def test_stops_after_enough_candidates_and_keeps_the_best_five(pipeline, monkeypatch):
    manager, network, set_nodes = pipeline
    monkeypatch.setattr(config, "BATCH_SIZE", 10)
    monkeypatch.setattr(config, "WORKERS", 5)
    set_nodes(make_nodes(["good"] * 60 + ["fast"] * 40))

    result = asyncio.run(manager.run_scan())

    assert result["working"] == config.CANDIDATE_COUNT == 25
    assert result["tested"] < 40  # the remaining batches were never started
    assert len(result["top"]) == config.TOP_COUNT == 5
    for node in result["top"]:
        assert node["attempts"] == config.DEEP_ATTEMPTS
        assert node["speed_mbps"] and node["exit_country"] == "DE"


def test_deep_checks_pick_the_best_and_drop_unreliable(pipeline):
    manager, network, set_nodes = pipeline
    set_nodes(make_nodes(["flaky", "slow", "good", "fast", "good", "good", "good"]))

    result = asyncio.run(manager.run_scan())

    hosts = [n["address"].split("-")[0] for n in result["top"]]
    assert hosts[0] == "fast"
    assert "flaky" not in hosts  # 3/5 is below DEEP_MIN_SUCCESS
    assert "slow" not in hosts  # outranked: same latency, far slower download
    assert len(hosts) == 5


def test_country_search_uses_the_measured_exit_country(pipeline):
    manager, network, set_nodes = pipeline
    # Labels lie: these claim France but exit in Germany, and vice versa.
    set_nodes(make_nodes(["fr"] * 10, label="DE") + make_nodes(["good"] * 6, source_index=1, label="FR"))

    result = asyncio.run(manager.run_scan(ScanQuery("FR")))

    assert result["country"] == "FR"
    assert result["working"] == 10
    assert all(n["exit_country"] == "FR" for n in result["top"])

    result = asyncio.run(manager.run_scan(ScanQuery("DE")))
    assert result["working"] == 6
    assert all(n["exit_country"] == "DE" for n in result["top"])


def test_order_candidates_interleaves_sources_and_prefers_country_hints():
    a = make_nodes(["good"] * 3, source_index=0, label="DE")
    b = make_nodes(["good"] * 2, source_index=1, label="FR")
    ordered = order_candidates(a + b, ScanQuery())
    assert [n.source_index for n in ordered] == [0, 1, 0, 1, 0]
    hinted = order_candidates(a + b, ScanQuery("FR"))
    assert [n.label_country for n in hinted[:2]] == ["FR", "FR"]


def test_scans_are_serialized(pipeline):
    manager, network, set_nodes = pipeline
    set_nodes(make_nodes(["good"] * 3))

    async def both():
        return await asyncio.gather(manager.run_scan(), manager.run_scan(ScanQuery("DE")))

    first, second = asyncio.run(both())
    assert first["working"] == second["working"] == 3
