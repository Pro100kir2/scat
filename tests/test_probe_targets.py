"""Security validation: probes must never become an SSRF proxy or a LAN scanner."""
import asyncio

import pytest

from app.probes import targets
from app.probes.targets import Target, TargetRejected, is_public_ip, resolve_public, target_for_node


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.8.9.10", "10.0.0.1", "172.16.5.4", "192.168.1.1", "169.254.169.254",  # metadata
    "100.64.0.1", "0.0.0.0", "255.255.255.255", "224.0.0.1", "192.0.2.10", "198.18.0.1", "240.0.0.1",
    "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1", "ff02::1", "2001:db8::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",  # IPv4-mapped
    "64:ff9b::a00:1", "64:ff9b::7f00:1", "64:ff9b::808:808",  # NAT64: the backend never needs it
    "2002:c0a8:0101::1",  # 6to4 around 192.168.1.1
    "fe80::1%eth0",
])
def test_private_and_special_addresses_are_rejected(ip):
    assert not is_public_ip(ip)
    with pytest.raises(TargetRejected):
        Target("TCP_CONNECT", ip, 443)


@pytest.mark.parametrize("ip", ["1.1.1.1", "8.8.8.8", "93.184.215.14", "2606:4700:4700::1111"])
def test_public_addresses_are_accepted(ip):
    assert is_public_ip(ip)
    assert Target("TCP_CONNECT", ip, 443).ip == ip


@pytest.mark.parametrize("port", [0, -1, 65536, 25, 445, 465, 587, 139, True, "443"])
def test_bad_and_abusive_ports_are_rejected(port):
    with pytest.raises(TargetRejected):
        Target("TCP_CONNECT", "1.1.1.1", port)


@pytest.mark.parametrize("name", ["localhost", "router.lan", "nas.local", "x.internal", "1.2.3.4", "-bad.com",
                                  "a..b.com", "single", "exa mple.com", "evil.com/path", "a" * 70 + ".com"])
def test_bad_server_names_are_rejected(name):
    with pytest.raises(TargetRejected):
        Target("TLS_CONNECT", "1.1.1.1", 443, server_name=name)


def test_job_shapes_are_strict():
    with pytest.raises(TargetRejected):
        Target("SHELL", "1.1.1.1", 443)  # type: ignore[arg-type]
    with pytest.raises(TargetRejected):
        Target("TCP_CONNECT", "1.1.1.1", 443, server_name="example.com")
    with pytest.raises(TargetRejected):
        Target("TLS_CONNECT", "1.1.1.1", 443)  # needs SNI
    with pytest.raises(TargetRejected):
        Target("HTTP_GET", "1.1.1.1", 443, server_name="example.com", path="/../etc/passwd")
    with pytest.raises(TargetRejected):
        Target("HTTP_GET", "1.1.1.1", 443, server_name="example.com", path="http://other/")
    with pytest.raises(TargetRejected):
        Target("TCP_CONNECT", "example.com", 443)  # hostnames never reach probes


def test_timeout_bounds():
    target = Target("TCP_CONNECT", "1.1.1.1", 443)
    assert target.job_payload(5000)["timeout_ms"] == 5000
    for bad in (0, 499, 10_001, 60_000):
        with pytest.raises(TargetRejected):
            target.job_payload(bad)


def test_payload_round_trip_and_key():
    target = Target("HTTP_GET", "1.1.1.1", 443, server_name="www.gstatic.com", path="/generate_204", tls=True)
    payload = target.job_payload()
    assert payload == {"type": "HTTP_GET", "host": "1.1.1.1", "port": 443, "timeout_ms": 5000,
                       "server_name": "www.gstatic.com", "path": "/generate_204", "tls": True}
    assert Target.from_payload(payload) == target
    assert target.key == "HTTP_GET|1.1.1.1|443|www.gstatic.com|https|/generate_204"


def _fake_dns(monkeypatch, answers):
    async def getaddrinfo(self, host, port, **kwargs):
        if host not in answers:
            raise OSError("NXDOMAIN")
        return [(2, 1, 6, "", (a, 0)) for a in answers[host]]

    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "getaddrinfo", getaddrinfo)


def test_dns_answers_are_validated(monkeypatch):
    _fake_dns(monkeypatch, {
        "good.example.org": ["2606:4700::1", "104.16.1.1"],
        "rebind.example.org": ["104.16.1.1", "127.0.0.1"],  # any private answer poisons the name
        "meta.example.org": ["169.254.169.254"],
    })
    assert asyncio.run(resolve_public("good.example.org")) == "104.16.1.1"  # IPv4 preferred
    for name in ("rebind.example.org", "meta.example.org", "missing.example.org"):
        with pytest.raises(TargetRejected):
            asyncio.run(resolve_public(name))
    with pytest.raises(TargetRejected):
        asyncio.run(resolve_public("10.1.2.3"))


def test_target_for_node_picks_the_check(monkeypatch):
    _fake_dns(monkeypatch, {"cdn.example.org": ["104.16.1.1"]})
    reality = {"address": "cdn.example.org", "port": 443, "security": "reality", "sni": "www.microsoft.com"}
    plain = {"address": "8.8.8.8", "port": 8080, "security": "none", "sni": ""}
    tls_no_sni = {"address": "cdn.example.org", "port": 2053, "security": "tls", "sni": ""}
    t1 = asyncio.run(target_for_node(reality))
    assert (t1.type, t1.ip, t1.server_name) == ("TLS_CONNECT", "104.16.1.1", "www.microsoft.com")
    assert asyncio.run(target_for_node(plain)) == Target("TCP_CONNECT", "8.8.8.8", 8080)
    assert asyncio.run(target_for_node(tls_no_sni)).server_name == "cdn.example.org"
    with pytest.raises(TargetRejected):
        asyncio.run(target_for_node({"address": "192.168.0.1", "port": 443, "security": "none"}))


def test_control_target(monkeypatch):
    _fake_dns(monkeypatch, {"www.gstatic.com": ["142.250.74.35"]})
    t = asyncio.run(targets.control_target("https://www.gstatic.com/generate_204"))
    assert (t.type, t.port, t.tls, t.path) == ("HTTP_GET", 443, True, "/generate_204")
    for bad in ("ftp://x.com/", "https://user:pw@www.gstatic.com/", "https://localhost/"):
        with pytest.raises(TargetRejected):
            asyncio.run(targets.control_target(bad))
