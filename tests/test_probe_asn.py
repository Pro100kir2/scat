"""Automatic network detection: the ASN of the address a probe registered from."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.probes import asn as asn_module
from app.probes.asn import AsnLookupError, Network, _parse
from tests.test_scoring import NODE_IPS, answer, approved_probe, entry_for


REAL_LOOKUP = asn_module.lookup  # captured at import, before the fixtures replace it


def run(coro):
    return asyncio.run(coro)


NETWORKS = {
    "95.24.10.1": Network(8359, "MTS PJSC", "95.24.0.0/13"),
    "95.24.10.2": Network(8359, "MTS PJSC", "95.24.0.0/13"),
    "85.140.1.1": Network(12389, "Rostelecom", "85.140.0.0/16"),
}


@pytest.fixture
def ripe(monkeypatch):
    calls = []

    async def lookup(ip):
        calls.append(ip)
        if ip not in NETWORKS:
            raise AsnLookupError("lookup failed: ClientConnectorError")
        return NETWORKS[ip]

    monkeypatch.setattr(asn_module, "lookup", lookup)
    return calls


def register(svc, public_ip, name="МТС", asn=None):
    e = svc.create_enrollment(name=name, country="RU", asn=asn)
    return svc.register(e["token"], agent_version="1.0.0", profile="linux-mipsle", platform="linux/mipsle",
                        public_ip=public_ip)["probe_id"]


def row(svc, probe_id):
    return svc.store.one("SELECT * FROM probes WHERE id=?", (probe_id,))


def test_asn_is_detected_from_the_registration_address(probe_service, ripe):
    probe_id = register(probe_service, "95.24.10.1", asn=None)  # nothing typed
    network = run(probe_service.detect_network(probe_id))
    assert network == NETWORKS["95.24.10.1"] and ripe == ["95.24.10.1"]
    r = row(probe_service, probe_id)
    assert (r["net_asn"], r["net_as_name"], r["net_prefix"], r["net_error"]) == (8359, "MTS PJSC", "95.24.0.0/13", None)
    admin = probe_service.admin_probe(probe_id)
    assert (admin["asn"], admin["asn_detected"], admin["as_name"], admin["asn_claimed"]) == (8359, 8359, "MTS PJSC", None)


def test_detected_asn_is_preferred_over_the_typed_one(probe_service, ripe):
    probe_id = register(probe_service, "85.140.1.1", asn=8359)  # typed MTS, really Rostelecom
    run(probe_service.detect_network(probe_id))
    admin = probe_service.admin_probe(probe_id)
    assert (admin["asn"], admin["asn_claimed"]) == (12389, 8359)
    probe_service.store.run("UPDATE probes SET status='approved' WHERE id=?", (probe_id,))
    assert probe_service.providers_overview()["probes"][0]["asn"] == 12389


def test_failed_lookup_falls_back_and_is_retried_later(probe_service, ripe):
    probe_id = register(probe_service, "203.0.113.200", asn=8359)
    assert run(probe_service.detect_network(probe_id)) is None
    r = row(probe_service, probe_id)
    assert r["net_asn"] is None and "lookup failed" in r["net_error"]
    admin = probe_service.admin_probe(probe_id)
    assert (admin["asn"], admin["network_error"]) == (8359, r["net_error"])  # typed ASN as fallback
    assert run(probe_service.detect_pending_networks()) == 0  # not yet due
    NETWORKS["203.0.113.200"] = Network(3216, "Beeline", None)
    try:
        probe_service.clock_.advance(3601)
        assert run(probe_service.detect_pending_networks()) == 1
        assert row(probe_service, probe_id)["net_asn"] == 3216
        assert run(probe_service.detect_pending_networks()) == 0  # detected: never looked up again
    finally:
        del NETWORKS["203.0.113.200"]


def test_registration_triggers_detection_without_waiting_for_it(probe_service, ripe, monkeypatch):
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", True)
    with TestClient(app) as client:
        token = client.post("/api/providers", json={"name": "МТС", "country": "RU"}).json()["token"]
        reply = client.post("/api/probes/register", headers={"X-Forwarded-For": "95.24.10.1"},
                            json={"token": token, "agent_version": "1.0.0", "profile": "linux-mipsle",
                                  "platform": "linux/mipsle"})
        assert reply.status_code == 201 and "asn" not in reply.json()
        client.get("/api/health")  # let the background task finish
    assert row(probe_service, reply.json()["probe_id"])["net_asn"] == 8359


def test_providers_inside_one_as_count_once(probe_service, ripe):
    """Anti-Sybil: two differently named "providers" registered from the same AS give one point."""
    entry = entry_for("A")
    run(probe_service.schedule_scan(entry))
    for name, ip in (("МТС", "95.24.10.1"), ("MTS home", "95.24.10.2"), ("Ростелеком", "85.140.1.1")):
        probe = approved_probe(probe_service, name, ip)
        run(probe_service.detect_network(probe["id"]))
        answer(probe_service, probe, {NODE_IPS["A"]: True})
    conf = probe_service.confirmations(["fp-A"])["fp-A"]
    assert (conf["score"], conf["total"]) == (2, 2)  # AS8359 once + AS12389


def test_parser_and_non_public_addresses():
    assert _parse({"data": {"resource": "95.24.0.0/13", "asns": [{"asn": 8359, "holder": "MTS PJSC"}]}}) == \
        Network(8359, "MTS PJSC", "95.24.0.0/13")
    for bad in ({}, {"data": {"asns": []}}, {"data": {"asns": [{"asn": "x"}]}}, {"data": {"asns": [{"asn": 0}]}}):
        with pytest.raises(AsnLookupError):
            _parse(bad)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "192.168.1.1", "", "not-an-ip"])
def test_real_lookup_refuses_non_public_addresses_without_any_request(ip):
    with pytest.raises(AsnLookupError, match="not a public address"):
        run(REAL_LOOKUP(ip))


def test_real_lookup_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "ASN_LOOKUP_URL", "")
    with pytest.raises(AsnLookupError, match="disabled"):
        run(REAL_LOOKUP("95.24.10.1"))
