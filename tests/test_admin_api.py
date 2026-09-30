"""What the admin panel does, over HTTP: probes, notifications, scanner, scans, cache,
settings, logs, health. Authentication itself is covered in test_admin_auth.py."""
import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config
from app.admin import settings as runtime_settings
from app.main import app
from app.probes.phone import InvalidPhone, normalize_phone
from app.probes.store import MIGRATIONS, SCHEMA_V1, Store
from app.services.scan_history import scan_history

TOKEN = "admin-" + "a" * 40
H = {"Authorization": f"Bearer {TOKEN}"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(api_service, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    for spec in runtime_settings.SPECS:  # settings write into app.config: restore after each test
        monkeypatch.setattr(config, spec, getattr(config, spec))
    monkeypatch.setattr(api_service, "max_queued", api_service.max_queued)
    from app.probes import api as probes_api

    monkeypatch.setattr(probes_api.enroll_limiter, "limit", 1000)
    api_service.history = scan_history
    return TestClient(app)


def enroll(client, name="МТС", phone=None, **extra):
    body = {"name": name, "country": "RU", **extra}
    if phone is not None:
        body["contact_phone"] = phone
    return client.post("/api/providers", json=body)


def register(client, token, profile="linux-mipsle"):
    return client.post("/api/probes/register", json={"token": token, "agent_version": "1.0.0", "profile": profile,
                                                     "platform": "linux/mipsle"}).json()


# --- phone ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("+7 (912) 345-67-89", "+79123456789"), ("8 912 345 67 89", "+79123456789"), ("89123456789", "+79123456789"),
    ("9123456789", "+79123456789"), ("0049 30 1234567", "+49301234567"), ("+375 29 123-45-67", "+375291234567"),
    ("", None), (None, None), ("   ", None),
])
def test_phone_normalisation(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["12345", "+0123456789", "call me", "+7912345678901234", "12345678901"])
def test_phone_rejections(raw):
    with pytest.raises(InvalidPhone):
        normalize_phone(raw)


def test_phone_is_stored_for_admins_and_nowhere_public(client, probe_service):
    assert enroll(client, phone="phone?").status_code == 422
    created = enroll(client, phone="8 (912) 345-67-89")
    assert created.status_code == 201 and "9123456789" not in created.text
    creds = register(client, created.json()["token"])
    probe_service.store.run("UPDATE probes SET status='approved' WHERE id=?", (creds["probe_id"],))
    detail = client.get(f"/api/admin/probes/{creds['probe_id']}", headers=H).json()
    assert detail["contact_phone"] == "+79123456789"
    public = [client.get("/api/providers").text, client.get("/api/results").text, client.get("/api/sources").text,
              client.post("/api/providers/enrollment", json={"enrollment_id": created.json()["enrollment_id"],
                                                             "token": created.json()["token"]}).text]
    assert not any("9123456789" in text for text in public)
    events = json.dumps(probe_service.store.all("SELECT * FROM events"))
    assert "9123456789" not in events
    # Admin search finds it however the digits are typed.
    for q in ("912345", "+7 912 345", "79123456789"):
        assert client.get("/api/admin/probes", params={"q": q}, headers=H).json()["total"] == 1, q
    patched = client.patch(f"/api/admin/probes/{creds['probe_id']}", headers=H, json={"contact_phone": "+49 30 1234567"})
    assert patched.json()["contact_phone"] == "+49301234567"
    assert client.patch(f"/api/admin/probes/{creds['probe_id']}", headers=H, json={"contact_phone": "x"}).status_code == 422


# --- probes list ------------------------------------------------------------------------------------
def test_probe_list_filters_sorting_and_pagination(client, probe_service):
    ids = []
    for i in range(7):
        creds = register(client, enroll(client, name=f"ISP {i}").json()["token"],
                         profile="linux-amd64" if i % 2 else "linux-mipsle")
        ids.append(creds["probe_id"])
        probe_service.clock_.advance(10)
    probe_service.store.run("UPDATE probes SET status='approved', jobs_done=?, jobs_error=3 WHERE id=?", (50, ids[0]))
    probe_service.store.run("UPDATE probes SET status='disabled' WHERE id=?", (ids[1],))
    probe_service.store.run("UPDATE probes SET last_seen=? WHERE id=?", (probe_service.clock_.now, ids[2]))
    page1 = client.get("/api/admin/probes", params={"per_page": 3}, headers=H).json()
    page3 = client.get("/api/admin/probes", params={"per_page": 3, "page": 3}, headers=H).json()
    assert page1["total"] == 7 and len(page1["items"]) == 3 and len(page3["items"]) == 1
    assert page1["items"][0]["id"] == ids[-1]  # newest first by default
    get = lambda **p: client.get("/api/admin/probes", params=p, headers=H).json()  # noqa: E731
    assert [p["id"] for p in get(status="approved")["items"]] == [ids[0]]
    assert get(status="pending")["total"] == 5
    assert [p["id"] for p in get(online="true")["items"]] == [ids[2]]
    assert get(online="false")["total"] == 6
    assert get(profile="linux-amd64")["total"] == 3
    assert get(sort="jobs_done")["items"][0]["id"] == ids[0]
    assert get(sort="error_count")["items"][0]["error_count"] == 3
    assert get(sort="created_at", order="asc")["items"][0]["id"] == ids[0]
    assert get(q=ids[3][4:12])["items"][0]["id"] == ids[3]
    assert set(get()["facets"]["profiles"]) == {"linux-amd64", "linux-mipsle"}
    assert client.get("/api/admin/probes", params={"sort": "secret_hash"}, headers=H).status_code == 422
    row = get(status="approved")["items"][0]
    for key in ("id", "status", "online", "verification", "profile", "version", "os", "arch", "cpu", "mem_total_kb",
                "storage_free_kb", "public_ip", "created_at", "last_heartbeat", "last_job_at", "last_success_at",
                "jobs_done", "jobs_success", "jobs_failed", "last_error", "contact_phone", "last_activity"):
        assert key in row


def test_probe_actions(client, probe_service):
    creds = register(client, enroll(client).json()["token"])
    pid = creds["probe_id"]
    assert client.post(f"/api/admin/probes/{pid}/enable", headers=H).status_code == 409  # not disabled
    assert client.post(f"/api/admin/probes/{pid}/approve", headers=H).json()["status"] == "approved"
    assert client.post(f"/api/admin/probes/{pid}/disable", headers=H).json()["status"] == "disabled"
    assert client.post(f"/api/admin/probes/{pid}/verify", headers=H).status_code == 409
    assert client.post(f"/api/admin/probes/{pid}/enable", headers=H).json()["status"] == "pending"
    assert client.get(f"/api/admin/probes/{pid}", headers=H).json()["verification"]["state"] == "none"
    assert client.post(f"/api/admin/probes/{pid}/verify", headers=H).json()["verification_id"]
    job = client.post(f"/api/admin/probes/{pid}/test", headers=H).json()["job_id"]
    jobs = client.get(f"/api/admin/probes/{pid}/jobs", headers=H).json()["items"]
    assert job in {j["id"] for j in jobs} and {"verify", "test"} <= {j["purpose"] for j in jobs}
    history = client.get(f"/api/admin/probes/{pid}/verifications", headers=H).json()["items"]
    assert history[0]["trigger"] == "admin"
    assert client.post(f"/api/admin/probes/{pid}/explode", headers=H).status_code == 422
    assert client.post("/api/admin/probes/prb_ffffffffffffffff/approve", headers=H).status_code == 404
    assert client.get("/api/admin/probes/../../etc", headers=H).status_code == 404
    # Delete: gone, credentials dead.
    assert client.delete(f"/api/admin/probes/{pid}", headers=H).json() == {"deleted": pid}
    auth = {"Authorization": f"Bearer {pid}.{creds['secret']}"}
    assert client.post("/api/probes/heartbeat", json={"agent_version": "1.0.0"}, headers=auth).status_code == 401
    assert probe_service.store.one("SELECT COUNT(*) AS n FROM jobs WHERE probe_id=?", (pid,))["n"] == 0
    kinds = [e["kind"] for e in client.get("/api/admin/events", params={"component": "admin"}, headers=H).json()["items"]]
    assert {"probe_approve", "probe_disable", "probe_enable", "probe_verify", "probe_test", "probe_deleted"} <= set(kinds)


# --- notifications ------------------------------------------------------------------------------------
def test_new_probe_notifications(client, probe_service):
    first = client.get("/api/admin/notifications", headers=H).json()  # the panel's first poll: no cursor
    assert first == {"latest_id": 0, "new": [], "unread_count": 0, "unread": []}
    creds = register(client, enroll(client).json()["token"])
    after = client.get("/api/admin/notifications", params={"after": first["latest_id"]}, headers=H).json()
    assert after["unread_count"] == 1 and after["unread"][0]["id"] == creds["probe_id"]
    assert len(after["new"]) == 1 and after["new"][0]["probe_id"] == creds["probe_id"]
    assert after["new"][0]["data"]["profile"] == "linux-mipsle" and after["new"][0]["data"]["status"] == "pending"
    # Polling again with the new cursor: nothing new, so no second sound.
    again = client.get("/api/admin/notifications", params={"after": after["latest_id"]}, headers=H).json()
    assert again["new"] == [] and again["unread_count"] == 1
    # Heartbeats and verification don't create new notifications.
    auth = {"Authorization": f"Bearer {creds['probe_id']}.{creds['secret']}"}
    client.post("/api/probes/heartbeat", json={"agent_version": "1.0.0"}, headers=auth)
    assert client.get("/api/admin/notifications", params={"after": after["latest_id"]}, headers=H).json()["new"] == []
    client.post("/api/admin/notifications/read", json={"probe_ids": [creds["probe_id"]]}, headers=H)
    assert client.get("/api/admin/notifications", headers=H).json()["unread_count"] == 0
    register(client, enroll(client, name="Второй").json()["token"])
    client.post("/api/admin/notifications/read", json={"all": True}, headers=H)
    assert client.get("/api/admin/notifications", headers=H).json()["unread_count"] == 0


# --- scanner, scans, cache ------------------------------------------------------------------------------
def test_scanner_start_run_cancel_restart(client, api_service, monkeypatch):
    monkeypatch.setattr(config, "AUTO_REFRESH", False)
    monkeypatch.setattr(config, "AUTO_SCAN_ON_STARTUP", False)
    api_service.scanner.delay = 2
    with TestClient(app) as c:  # one event loop for the whole scenario, as in production
        assert c.post("/api/admin/scanner/start", headers=H).json()["outcome"] == "started"
        assert c.post("/api/admin/scanner/run", headers=H).json()["outcome"] == "joined"  # never two at once
        status = c.get("/api/admin/scanner", headers=H).json()
        assert [q["query"] for q in status["queue"]] == ["all"] and status["current_scan"]["status"] == "running"
        restarted = c.post("/api/admin/scanner/restart", headers=H).json()
        assert restarted == {"cancelled": ["all"], "outcome": "started"}
        assert c.post("/api/admin/scanner/cancel", headers=H).json() == {"cancelled": ["all"]}
        assert c.post("/api/admin/scanner/cancel", headers=H).status_code == 409
        statuses = [s["status"] for s in c.get("/api/admin/scans", headers=H).json()["items"]]
        assert statuses[:2] == ["cancelled", "cancelled"]


def test_scan_history_records_outcomes(api_service):
    api_service.history = scan_history

    async def scenario():
        api_service.request_scan(trigger="admin")
        await api_service.wait()
        api_service.scanner.fail = True
        api_service.request_scan(force=True, trigger="auto")
        await api_service.wait()

    run(scenario())
    items = scan_history.list()["items"]
    assert [(s["status"], s["trigger"]) for s in items] == [("failed", "auto"), ("completed", "admin")]
    done = scan_history.get(items[1]["id"])
    assert (done["unique_count"], done["tested_count"], done["passed_count"], done["failed_count"]) == (100, 60, 25, 35)
    assert done["detail"]["top"][0]["fingerprint"] == "fp0"
    assert items[0]["error"] == "boom"


def test_cancel_a_running_scan(api_service):
    api_service.history = scan_history
    api_service.scanner.delay = 5

    async def scenario():
        api_service.request_scan(force=True, trigger="admin")
        await asyncio.sleep(0.05)
        assert api_service.running() == [{"query": "all", "state": "running"}] or api_service.running()
        assert api_service.cancel() == ["all"]
        for _ in range(50):
            if not api_service.running():
                break
            await asyncio.sleep(0.02)
        assert api_service.running() == []

    run(scenario())
    assert scan_history.list()["items"][0]["status"] == "cancelled"
    assert api_service.entry() is None  # the cache was not touched


def test_cache_views_and_actions(client, api_service):
    run(_scan(api_service))
    cache = client.get("/api/admin/cache", headers=H).json()
    assert cache["files"][0]["query"] == "all" and cache["files"][0]["size_bytes"] > 0
    assert cache["ranking"][0]["fingerprint"] == "fp0" and cache["ranking"][0]["in_top"] is True
    assert client.post("/api/admin/cache/refresh", headers=H).json()["status"] == "refreshed"
    assert client.post("/api/admin/cache/clear", headers=H).json() == {"status": "cleared"}
    assert client.get("/api/admin/cache", headers=H).json()["files"] == []
    assert client.get("/api/results").json()["nodes"] == []  # no stale ranking survives a clear


async def _scan(service):
    service.request_scan(force=True, trigger="admin")
    await service.wait()


def test_dashboard_and_health(client, api_service, probe_service):
    run(_scan(api_service))
    register(client, enroll(client).json()["token"])
    dash = client.get("/api/admin/dashboard", headers=H).json()
    assert dash["probes"]["pending"] == 1 and dash["probes"]["new"] == 1
    assert dash["cache"]["state"] == "fresh" and dash["recent_events"]
    health = client.get("/api/admin/health", headers=H).json()
    assert set(health["components"]) >= {"backend", "database", "scanner", "probes", "sources", "cache", "disk"}
    assert health["components"]["database"]["status"] == "ok"
    assert health["components"]["sources"]["status"] == "error"  # no sources configured in tests
    assert health["status"] == "error" and health["versions"]["scanner"] == config.SCANNER_VERSION


# --- settings & events ----------------------------------------------------------------------------------
def test_settings(client, api_service):
    items = {s["key"]: s for s in client.get("/api/admin/settings", headers=H).json()["items"]}
    for secret in ("ADMIN_TOKEN", "ALLOWED_ORIGINS", "PUBLIC_API_URL", "TRUST_PROXY_HEADERS", "PROBE_DB_PATH"):
        assert secret not in items
    reply = client.put("/api/admin/settings", headers=H, json={"TOP_COUNT": 7, "CACHE_TTL_SECONDS": 3600,
                                                              "RANKING_USE_PROBE_SCORE": False})
    assert reply.status_code == 200
    assert (config.TOP_COUNT, config.RANKING_USE_PROBE_SCORE, api_service.ttl_seconds) == (7, False, 3600)
    for bad in ({"TOP_COUNT": 0}, {"TOP_COUNT": "many"}, {"TOP_COUNT": 2.5}, {"ADMIN_TOKEN": "x"},
                {"DEEP_MIN_SUCCESS": 9, "DEEP_ATTEMPTS": 5}, {"RANKING_USE_PROBE_SCORE": "maybe"}, {}):
        assert client.put("/api/admin/settings", headers=H, json=bad).status_code == 422, bad
    assert config.TOP_COUNT == 7  # a rejected update changed nothing
    # Stored overrides survive a restart.
    config.TOP_COUNT = 5
    runtime_settings.load()
    assert config.TOP_COUNT == 7
    assert client.delete("/api/admin/settings/TOP_COUNT", headers=H).status_code == 200
    assert config.TOP_COUNT == runtime_settings.DEFAULTS["TOP_COUNT"]
    assert client.delete("/api/admin/settings/ADMIN_TOKEN", headers=H).status_code == 404


def test_event_filters(client, probe_service):
    from app import events

    events.record("source", "source_failed", "Source “x” failed: timeout", severity="warning", source_id=3)
    events.record("scanner", "scan_failed", "Scan failed: boom", severity="error")
    events.record("auth", "admin_login", "token skenr_abcdef and Bearer 123 must not be kept",
                  data={"token": "secret", "phone": "+79120000000", "ok": 1})
    get = lambda **p: client.get("/api/admin/events", params=p, headers=H).json()  # noqa: E731
    assert [e["kind"] for e in get(severity="warning")["items"]] == ["scan_failed", "source_failed"]
    assert get(component="source", source_id=3)["total"] == 1
    assert get(q="boom")["items"][0]["kind"] == "scan_failed"
    leaked = get(component="auth")["items"][0]
    assert "skenr_abcdef" not in leaked["message"] and "123" not in leaked["message"]
    assert leaked["data"] == {"token": "[redacted]", "phone": "[redacted]", "ok": 1}
    assert client.get("/api/admin/events", params={"component": "nope"}, headers=H).status_code == 422


def test_providers_view(client, probe_service):
    creds = register(client, enroll(client).json()["token"])
    probe_service.store.run("UPDATE probes SET status='approved', jobs_success=5, jobs_failed=2 WHERE id=?",
                            (creds["probe_id"],))
    (row,) = client.get("/api/admin/providers", headers=H).json()["items"]
    assert (row["name"], row["probes_approved"], row["jobs_success"], row["jobs_failed"], row["public"]) == \
        ("МТС", 1, 5, 2, True)


# --- migrations -------------------------------------------------------------------------------------------
def test_v1_database_is_migrated_without_losing_rows(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_V1)
    conn.execute("PRAGMA user_version=1")
    conn.execute("INSERT INTO providers VALUES ('prv_1','МТС','мтс','RU',NULL,NULL,NULL,1)")
    conn.execute("INSERT INTO probes (id, provider_id, status, secret_hash, credential_at, created_at)"
                 " VALUES ('prb_1','prv_1','approved','h',1,1), ('prb_2','prv_1','pending','h',1,2)")
    conn.execute("INSERT INTO jobs (id, probe_id, key, type, payload, is_control, status, created_at, expires_at)"
                 " VALUES ('job_1','prb_1','k','HTTP_GET','{}',1,'queued',1,9)")
    conn.commit()
    conn.close()
    store = Store(path)
    assert store.one("PRAGMA user_version")["user_version"] == max(MIGRATIONS)
    probes = {r["id"]: r for r in store.all("SELECT * FROM probes")}
    assert probes["prb_1"]["verify_state"] == "passed" and probes["prb_2"]["verify_state"] == "none"
    assert all(p["admin_seen_at"] is not None for p in probes.values())  # old probes are not "new"
    assert store.one("SELECT purpose FROM jobs")["purpose"] == "control"
    assert store.one("SELECT name FROM providers")["name"] == "МТС"
    store.close()
    assert Store(path).one("PRAGMA user_version")["user_version"] == max(MIGRATIONS)  # idempotent
