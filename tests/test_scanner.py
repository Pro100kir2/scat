import asyncio
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app import config
from app.services import probes
from app.services import scanner as scanner_module
from app.services.presenter import clean_label
from urllib.parse import parse_qs, urlsplit

from app.services.scanner import (
    ScanQuery,
    VLESSScannerManager,
    build_outbound,
    build_xray_config,
    country_from_name,
    link_matches_tested_config,
    node_result,
    order_candidates,
    parse_vless,
)
from tests.conftest import REALITY_LINK

UUID = "11111111-2222-3333-4444-555555555555"
PBK = "Yx0lZ5bqG3N5M5xk0pWkZxQy3qvC0d5c2mV9rBzH1Qw"


def link_params(link: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(link).query, keep_blank_values=True).items()}


# --- parsing ----------------------------------------------------------------------


def test_parse_reality_link():
    node = parse_vless(REALITY_LINK)
    assert node.address == "example.com"
    assert node.security == "reality"
    assert node.fingerprint_tls == "chrome"
    assert node.short_id == "6ba85179e30d4fc2"
    assert node.label_country == "DE"


def test_unknown_reality_fingerprint_falls_back_to_the_tested_one():
    node = parse_vless(REALITY_LINK.replace("fp=chrome", "fp=hellochrome_120"))
    reality = build_xray_config([node])["outbounds"][0]["streamSettings"]["realitySettings"]
    assert reality["fingerprint"] == "chrome"
    assert link_params(node.link)["fp"] == "chrome"  # the link says what was tested


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
    node = parse_vless(f"vless://{UUID}@h.example.com:443?type=ws&security=tls&alpn=h2,v2rayNplus--junk,http/1.1")
    assert node.alpn == ["h2", "http/1.1"]
    assert link_params(node.link)["alpn"] == "h2,http/1.1"


# --- share links: what users get must be exactly what was tested ------------------

LINKS = {
    "reality-vision": f"vless://{UUID}@r.example.com:443?type=tcp&security=reality&sni=www.microsoft.com"
                      f"&fp=firefox&pbk={PBK}&sid=6ba85179e30d4fc2&spx=%2F&flow=xtls-rprx-vision#%F0%9F%87%A9%F0%9F%87%AA%20DE",
    "reality-no-sid": f"vless://{UUID}@r.example.com:443?type=tcp&security=reality&sni=www.microsoft.com&pbk={PBK}",
    "reality-aliases": f"vless://{UUID}@r.example.com:443?network=raw&security=reality&serverName=www.microsoft.com"
                       f"&publicKey={PBK}&shortId=ab&fingerprint=safari&spiderX=%2Fx",
    "ws-tls": f"vless://{UUID}@w.example.com:2053?type=ws&security=tls&sni=cdn.example.com&host=cdn.example.com"
              "&path=%2Fws%3Fed%3D2048&fp=chrome&alpn=h2,http/1.1&allowInsecure=1",
    "ws-plain-unencoded-path": f"vless://{UUID}@57.129.41.216:80?security=none&type=ws&path=/vless/&host=OV-Germany1.09vpn.com",
    "grpc-multi": f"vless://{UUID}@g.example.com:443?type=grpc&security=tls&serviceName=my%20svc&mode=multi&sni=g.example.com",
    "xhttp": f"vless://{UUID}@x.example.com:443?type=splithttp&security=tls&path=%2Fxh&host=x.example.com&mode=packet-up",
    "httpupgrade": f"vless://{UUID}@u.example.com:80?type=http-upgrade&path=%2Fup&host=u.example.com",
    "ipv6": f"vless://{UUID}@[2001:db8::1]:8443?type=tcp&security=tls&sni=v6.example.com",
    "html-escaped": f"vless://{UUID}@r.example.com:443?type=tcp&amp;security=reality&amp;sni=www.microsoft.com&amp;pbk={PBK}",
    "raw-fragment": f"vless://{UUID}@31.76.76.28:443?path=%2F&security=&encryption=none&type=ws#🇳🇱[openproxylist.com",
}


@pytest.mark.parametrize("name", LINKS)
def test_share_link_reproduces_the_tested_config(name):
    node = parse_vless(LINKS[name])
    assert link_matches_tested_config(node)
    # Idempotent: a link rebuilt from the share link is the share link.
    assert parse_vless(node.link).link == node.link
    # Strict RFC 3986: no raw spaces, emoji or brackets outside an IPv6 host.
    parts = urlsplit(node.link)
    assert re.fullmatch(r"[A-Za-z0-9\-._~%:/?#@=&+,;!$'()*]*", node.link.replace(f"[{node.address}]", ""))
    assert parts.port == node.port and parts.hostname == node.address.lower()


def test_share_link_uses_the_real_server_port_not_the_scanner_port():
    nodes = [parse_vless(link) for link in LINKS.values()]
    build_xray_config(nodes)  # assigns local SOCKS ports 20000+
    for node in nodes:
        assert node.local_port >= config.PORT_START
        assert urlsplit(node.link).port == node.port != node.local_port
        outbound = build_xray_config([node])["outbounds"][0]
        assert outbound["settings"]["vnext"][0]["port"] == node.port
    assert "local_port" not in node_result(nodes[0])


def test_aliases_become_standard_parameters():
    params = link_params(parse_vless(LINKS["reality-aliases"]).link)
    assert params["type"] == "tcp"
    assert params["sni"] == "www.microsoft.com"
    assert params["pbk"] == PBK and params["sid"] == "ab"
    assert params["fp"] == "safari" and params["spx"] == "/x"
    assert not {"serverName", "publicKey", "shortId", "fingerprint", "network", "spiderX"} & set(params)


def test_reality_without_short_id_is_tested_and_shared_without_one():
    node = parse_vless(LINKS["reality-no-sid"])
    reality = build_outbound(node, "t")["streamSettings"]["realitySettings"]
    assert "shortId" not in reality and "sid" not in link_params(node.link)
    assert reality["fingerprint"] == link_params(node.link)["fp"] == "chrome"


def test_parameters_the_scanner_does_not_test_are_not_shared():
    link = (f"vless://{UUID}@h.example.com:443?type=tcp&headerType=http&host=%2F%3FTelegram&path=%2Fx"
            f"&security=reality&sni=h.example.com&pbk={PBK}&alpn=h2&allowInsecure=1&serviceName=x&extra=%7B%7D")
    params = link_params(parse_vless(link).link)
    assert not {"headerType", "host", "path", "alpn", "allowInsecure", "serviceName", "extra"} & set(params)
    plain = link_params(parse_vless(f"vless://{UUID}@h.example.com:80?type=grpc&sni=%2Fjunk&fp=chrome&pbk={PBK}").link)
    assert plain["security"] == "none" and not {"sni", "fp", "pbk"} & set(plain)


def test_encoding_round_trips_special_characters():
    node = parse_vless(f"vless://{UUID}@h.example.com:80?type=ws&path=%2Fa%2Bb%26c%3D1%23x&host=h.example.com#name%20with%20%26")
    assert node.path == "/a+b&c=1#x"
    assert "%2Fa%2Bb%26c%3D1%23x" in node.link
    again = parse_vless(node.link)
    assert again.path == node.path and again.name == "name with &"


def test_vless_encryption_the_scanner_cannot_speak_is_rejected():
    with pytest.raises(ValueError):
        parse_vless(f"vless://{UUID}@h.example.com:443?type=tcp&security=tls&encryption=mlkem768x25519plus.native.0rtt.abc")


@pytest.mark.skipif(
    not (Path(config.XRAY_BINARY).exists() or shutil.which("xray")),
    reason="xray binary not installed",
)
def test_xray_accepts_every_share_link_shape():
    manager = VLESSScannerManager()
    nodes = [parse_vless(link) for link in LINKS.values()]
    ok, reason = asyncio.run(manager._xray_accepts(manager.ensure_xray_binary(), nodes))
    assert ok, reason


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
