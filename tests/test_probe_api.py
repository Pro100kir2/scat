"""HTTP contract of /api/providers and /api/probes, as the website and the agent use it."""
import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app, schedule_probe_round
from app.services.scan_service import ALL
from tests.conftest import FakeScanner, public_node_at

SUPPORTED_DIAG = {"os": "Linux", "kernel": "5.15.137", "machine": "mips", "elf_class": 32, "endian": "little",
                  "openwrt": True, "openwrt_arch": "mipsel_24kc", "openwrt_version": "23.05.3",
                  "cpu_model": "MediaTek MT7621 ver:1 eco:3", "cpu_cores": 4, "mem_total_kb": 249000,
                  "mem_available_kb": 180000, "storage_free_kb": 90000, "init": "procd", "wget_https": True,
                  "busybox": True, "is_root": True, "ipv4": True, "ipv6": False}


@pytest.fixture
def client(api_service):
    return TestClient(app)


def enroll(client, **overrides):
    body = {"name": "МТС", "country": "ru", "region": "Москва", "asn": "AS8359", "info": ""}
    body.update(overrides)
    return client.post("/api/providers", json=body)


def register(client, token):
    return client.post("/api/probes/register", json={"token": token, "agent_version": "1.0.0",
                                                     "profile": "linux-mipsle", "platform": "linux/mipsle"})


def auth(creds):
    return {"Authorization": f"Bearer {creds['probe_id']}.{creds['secret']}"}


def online_probe(client, probe_service, name="МТС"):
    token = enroll(client, name=name).json()["token"]
    creds = register(client, token).json()
    probe_service.store.run("UPDATE probes SET status='approved' WHERE id=?", (creds["probe_id"],))
    assert client.post("/api/probes/heartbeat", json={"agent_version": "1.0.0"}, headers=auth(creds)).status_code == 200
    return creds


# --- website -------------------------------------------------------------------------------
def test_add_provider_returns_token_and_install_commands(client):
    res = enroll(client)
    assert res.status_code == 201
    data = res.json()
    assert data["provider"] == {"id": data["provider"]["id"], "name": "МТС", "country": "RU",
                                "region": "Москва", "asn": 8359}
    assert data["install"]["curl"].endswith(f"| sh -s -- --token {data['token']}")
    assert "/api/probes/install.sh" in data["install"]["wget"]
    assert res.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("override", [
    {"name": "x"}, {"name": "https://spam.example"}, {"name": "<b>ISP</b>"}, {"country": "RUS"},
    {"asn": "ASX"}, {"asn": "99999999999"}, {"info": "visit www.spam.com"}, {"extra": "field"},
])
def test_add_provider_validation(client, override):
    assert enroll(client, **override).status_code == 422


def test_add_provider_rate_limit(client):
    codes = [enroll(client, name=f"ISP {i}").status_code for i in range(config.PROBE_ENROLLMENTS_PER_HOUR + 1)]
    assert codes[-1] == 429 and set(codes[:-1]) == {201}


def test_enrollment_status_endpoint(client):
    data = enroll(client).json()
    body = {"enrollment_id": data["enrollment_id"], "token": data["token"]}
    assert client.post("/api/providers/enrollment", json=body).json()["state"] == "waiting"
    client.post("/api/probes/diagnostics", json={"token": data["token"], "diagnostics": SUPPORTED_DIAG})
    status = client.post("/api/providers/enrollment", json=body).json()
    assert status["state"] == "installing" and status["diagnostics"]["profile"] == "linux-mipsle"
    assert client.post("/api/providers/enrollment", json={**body, "token": "skenr_x"}).status_code == 404


def test_providers_page_data(client, probe_service):
    online_probe(client, probe_service, "Билайн")
    data = client.get("/api/providers").json()
    (provider,) = data["providers"]
    assert provider["name"] == "Билайн" and provider["status"] == "online" and provider["probes_online"] == 1
    (probe,) = data["probes"]
    assert set(probe) == {"id", "provider", "status", "online", "last_seen", "region", "asn", "platform", "version"}
    assert probe["platform"] == "Linux MIPS little-endian (softfloat)"


# --- installer -----------------------------------------------------------------------------
def test_diagnostics_env_format_is_safe_for_sh(client):
    res = client.post("/api/probes/diagnostics?format=env", json={"diagnostics": SUPPORTED_DIAG})
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/plain")
    lines = dict(line.split("=", 1) for line in res.text.strip().splitlines())
    assert lines["verdict"] == "SUPPORTED" and lines["profile"] == "linux-mipsle" and lines["service"] == "procd"
    assert lines["url"].endswith("/api/probes/download/linux-mipsle") and len(lines["sha256"]) == 64
    bad = client.post("/api/probes/diagnostics?format=env",
                      json={"diagnostics": {**SUPPORTED_DIAG, "machine": "`rm -rf /`$(id)", "mem_total_kb": 1000}})
    assert "verdict=UNSUPPORTED" in bad.text
    for char in "`$'\"\\":
        assert char not in bad.text


def test_diagnostics_ignore_unknown_fields_but_reject_bad_types(client):
    ok = client.post("/api/probes/diagnostics", json={"diagnostics": {**SUPPORTED_DIAG, "surprise": 1}})
    assert ok.status_code == 200
    assert client.post("/api/probes/diagnostics", json={"diagnostics": {"mem_total_kb": "lots"}}).status_code == 422


def test_releases_download_and_checksum(client):
    releases = client.get("/api/probes/releases").json()
    assert releases["version"] == "1.0.0"
    mipsle = next(r for r in releases["releases"] if r["profile"] == "linux-mipsle")
    binary = client.get("/api/probes/download/linux-mipsle")
    assert binary.status_code == 200
    assert hashlib.sha256(binary.content).hexdigest() == mipsle["sha256"] == binary.headers["x-checksum-sha256"]
    assert client.get("/api/probes/download/../../etc/passwd").status_code == 404
    assert client.get("/api/probes/download/linux-sparc").status_code == 404


def test_install_script_is_templated(client, tmp_path, monkeypatch):
    script = tmp_path / "install.sh"
    script.write_text('#!/bin/sh\nAPI="__SCAT_API_URL__"\n')
    monkeypatch.setattr(config, "PROBE_INSTALLER", script)
    monkeypatch.setattr(config, "PUBLIC_API_URL", "https://api.scat-search.ru")
    res = client.get("/api/probes/install.sh")
    assert res.status_code == 200 and 'API="https://api.scat-search.ru"' in res.text


# --- agent protocol ---------------------------------------------------------------------------
def test_register_heartbeat_jobs_results_round_trip(client, probe_service):
    creds = online_probe(client, probe_service)
    asyncio.run(probe_service.schedule_scan({"created_at": "x", "top": [public_node_at(0, "1.1.1.1")],
                                             "ranked": [public_node_at(0, "1.1.1.1")]}))
    jobs = client.get("/api/probes/jobs", headers=auth(creds)).json()["jobs"]
    assert [j["type"] for j in jobs] == ["HTTP_GET", "TLS_CONNECT"]
    body = {"results": [{"job_id": j["job_id"], "outcome": "SUCCESS", "latency_ms": 31.5} for j in jobs]}
    assert client.post("/api/probes/results", json=body, headers=auth(creds)).json() == {"accepted": 2, "rejected": []}


def test_agent_auth_errors(client, probe_service):
    creds = online_probe(client, probe_service)
    assert client.get("/api/probes/jobs").status_code == 401
    assert client.get("/api/probes/jobs", headers={"Authorization": "Bearer prb_0123456789abcdef.x"}).status_code == 401
    assert register(client, "skenr_nope").status_code == 403
    probe_service.store.run("UPDATE probes SET status='disabled' WHERE id=?", (creds["probe_id"],))
    assert client.get("/api/probes/jobs", headers=auth(creds)).status_code == 403


@pytest.mark.parametrize("item", [
    {"job_id": "job_" + "0" * 20, "outcome": "MAYBE"},
    {"job_id": "not-a-job", "outcome": "SUCCESS"},
    {"job_id": "job_" + "0" * 20, "outcome": "SUCCESS", "command": "reboot"},
    {"job_id": "job_" + "0" * 20, "outcome": "SUCCESS", "latency_ms": -1},
    {"job_id": "job_" + "0" * 20, "outcome": "FAILED", "error": "Connection refused; rm -rf"},
])
def test_result_schema_is_strict(client, probe_service, item):
    creds = online_probe(client, probe_service)
    assert client.post("/api/probes/results", json={"results": [item]}, headers=auth(creds)).status_code == 422


def test_body_size_limits(client, probe_service):
    creds = online_probe(client, probe_service)
    huge = {"results": [], "pad": "x" * 70_000}
    assert client.post("/api/probes/results", json=huge, headers=auth(creds)).status_code == 413
    too_many = {"results": [{"job_id": "job_" + "0" * 20, "outcome": "SUCCESS"}] * 51}
    assert client.post("/api/probes/results", json=too_many, headers=auth(creds)).status_code == 422


def test_rotate_endpoint(client, probe_service):
    creds = online_probe(client, probe_service)
    new = client.post("/api/probes/rotate", headers=auth(creds)).json()
    assert new["probe_id"] == creds["probe_id"] and new["secret"] != creds["secret"]
    assert client.post("/api/probes/heartbeat", json={"agent_version": "1.0.1"}, headers=auth(new)).status_code == 200


def test_admin_approval(client, probe_service, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "admin-secret")
    creds = register(client, enroll(client).json()["token"]).json()
    admin = {"Authorization": "Bearer admin-secret"}
    assert client.get("/api/admin/probes").status_code == 401
    assert client.get("/api/admin/probes", headers=admin).json()["probes"][0]["status"] == "pending"
    res = client.post(f"/api/admin/probes/{creds['probe_id']}/approve", headers=admin)
    assert res.json() == {"probe_id": creds["probe_id"], "status": "approved"}
    assert client.post(f"/api/admin/probes/{creds['probe_id']}/delete", headers=admin).status_code == 422
    assert client.post("/api/admin/probes/prb_0000000000000000/approve", headers=admin).status_code == 404


# --- integration with the scan pipeline -------------------------------------------------------
def test_page_loads_never_schedule_probe_work(client, api_service, probe_service):
    online_probe(client, probe_service)
    asyncio.run(_fill(api_service))
    before = probe_service.store.one("SELECT COUNT(*) AS n FROM jobs")["n"]
    for _ in range(3):
        client.get("/api/results")
        client.get("/api/providers")
    assert probe_service.store.one("SELECT COUNT(*) AS n FROM jobs")["n"] == before == 0


async def _fill(service):
    service.request_scan()
    await service.wait()


def test_full_scan_triggers_one_round_and_results_show_top5_reachability(client, make_service, probe_service, monkeypatch):
    from app.api import routes

    nodes = [public_node_at(i, ip) for i, ip in enumerate(["1.1.1.1", "8.8.8.8", "9.9.9.9", "104.16.1.1", "151.101.1.1",
                                                           "93.184.215.14", "208.67.222.222"])]
    scanner = FakeScanner(nodes=nodes[:5])
    original = scanner.run_scan

    async def run_scan(query=None):
        result = await original(query)
        return {**result, "ranked": nodes}

    scanner.run_scan = run_scan
    service = make_service(scanner=scanner)
    service.add_listener(schedule_probe_round)
    monkeypatch.setattr(routes, "scan_service", service)
    creds = online_probe(client, probe_service)

    async def scan_and_settle():
        service.request_scan(ALL)
        await service.wait(ALL)
        await asyncio.gather(*service._listener_tasks)

    asyncio.run(scan_and_settle())
    targets = probe_service.store.one("SELECT COUNT(*) AS n FROM targets")["n"]
    assert targets == 7  # all ranked candidates (<= 25), not just the top 5

    jobs = client.get("/api/probes/jobs?limit=50", headers=auth(creds)).json()["jobs"]
    client.post("/api/probes/results", headers=auth(creds), json={"results": [
        {"job_id": j["job_id"], "outcome": "SUCCESS", "latency_ms": 20} for j in jobs]})

    data = client.get("/api/results").json()
    assert len(data["nodes"]) == 5
    for node in data["nodes"]:
        assert node["reachability"] == [{"provider_id": node["reachability"][0]["provider_id"], "provider": "МТС",
                                         "state": "SUCCESS", "latency_ms": 20,
                                         "checked_at": node["reachability"][0]["checked_at"], "probes": 1}]
    # Existing public fields are untouched.
    assert {"id", "rank", "link", "latency_ms", "speed_mbps", "stable"} <= set(data["nodes"][0])
