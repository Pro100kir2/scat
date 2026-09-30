"""Automatic probe verification: register → pending → verification job → the probe really
fetches the one-time URL → approved. And every way it can fail, visibly."""
import asyncio

from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.probes.service import VERIFY_PATH
from tests.conftest import API_IP, public_node_at


def run(coro):
    return asyncio.run(coro)


def register(svc, name="МТС", public_ip="198.51.100.20"):
    enrollment = svc.create_enrollment(name=name, country="RU")
    creds = svc.register(enrollment["token"], agent_version="1.0.0", profile="linux-mipsle",
                         platform="linux/mipsle", public_ip=public_ip)
    return enrollment, creds, svc.authenticate(f"Bearer {creds['probe_id']}.{creds['secret']}")


def row(svc, probe_id):
    return svc.store.one("SELECT * FROM probes WHERE id=?", (probe_id,))


def verification_jobs(svc, probe):
    jobs = run(svc.lease_jobs(probe, limit=50))
    nonce_job = next(j for j in jobs if j.get("path", "").startswith(VERIFY_PATH))
    return jobs, nonce_job


def fetch_nonce(job):
    """What the agent's HTTP_GET does: the request reaches the API's verify endpoint."""
    client = TestClient(app)
    reply = client.get(job["path"], headers={"Host": job["server_name"]})
    assert reply.status_code == 204 and reply.content == b""


def test_register_verify_approve(probe_service):
    _, creds, probe = register(probe_service)
    assert row(probe_service, creds["probe_id"])["status"] == "pending"
    run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="198.51.100.20"))  # first contact
    assert row(probe_service, creds["probe_id"])["verify_state"] == "running"
    jobs, job = verification_jobs(probe_service, probe)
    # A normal typed job: HTTP_GET to this API's public address, nothing the agent does not know.
    assert set(job) <= {"job_id", "type", "host", "port", "timeout_ms", "server_name", "path", "tls"}
    assert (job["type"], job["host"], job["port"], job["server_name"], job["tls"]) == \
        ("HTTP_GET", API_IP, 443, "api.scat-search.test", True)
    fetch_nonce(job)
    probe_service.submit_results(probe, [{"job_id": j["job_id"], "outcome": "SUCCESS", "http_status": 204}
                                         for j in jobs])
    after = row(probe_service, creds["probe_id"])
    assert (after["status"], after["verify_state"], after["verify_reason"]) == ("approved", "passed", None)
    (history,) = probe_service.admin_verifications(creds["probe_id"])
    assert history["state"] == "passed" and history["trigger"] == "first_contact"
    assert any(c["purpose"] == "verify" and c["request_seen"] for c in history["detail"]["checks"])
    kinds = [e["kind"] for e in probe_service.store.all("SELECT kind FROM events ORDER BY id")]
    assert kinds[:3] == ["probe_registered", "verification_started", "probe_verified"]


def test_verified_probe_starts_working_immediately(probe_service):
    entry = {"created_at": "x", "ranked": [public_node_at(0, "93.184.215.14")], "top": []}
    run(probe_service.schedule_scan(entry))
    _, creds, probe = register(probe_service)
    run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1.2.3.4"))
    jobs, job = verification_jobs(probe_service, probe)
    assert all(j["type"] == "HTTP_GET" for j in jobs)  # pending: no scan targets yet
    fetch_nonce(job)
    probe_service.submit_results(probe, [{"job_id": j["job_id"], "outcome": "SUCCESS", "http_status": 204} for j in jobs])
    assert run(probe_service.flush_activations()) == 1
    assert [j["type"] for j in run(probe_service.lease_jobs(probe, limit=50))] == ["TLS_CONNECT"]


def test_claiming_success_without_the_request_fails(probe_service):
    """A fake client that answers SUCCESS to everything never becomes approved."""
    enrollment, creds, probe = register(probe_service)
    run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1.2.3.4"))
    jobs, _ = verification_jobs(probe_service, probe)
    probe_service.submit_results(probe, [{"job_id": j["job_id"], "outcome": "SUCCESS", "http_status": 204} for j in jobs])
    after = row(probe_service, creds["probe_id"])
    assert (after["status"], after["verify_state"], after["verify_reason"]) == ("pending", "failed", "nonce_not_received")
    status = probe_service.enrollment_status(enrollment["enrollment_id"], enrollment["token"])
    assert status["state"] == "verification_failed"
    assert status["verification"]["reason"] == "nonce_not_received" and status["verification"]["will_retry"] is True


def test_nonce_of_another_probe_or_a_used_job_does_not_help(probe_service):
    _, creds_a, a = register(probe_service, "A ISP", "198.51.100.1")
    _, creds_b, b = register(probe_service, "B ISP", "198.51.100.2")
    run(probe_service.heartbeat(a, agent_version="1.0.0", public_ip="1"))
    run(probe_service.heartbeat(b, agent_version="1.0.0", public_ip="2"))
    jobs_a, job_a = verification_jobs(probe_service, a)
    jobs_b, _ = verification_jobs(probe_service, b)
    fetch_nonce(job_a)  # A's URL was fetched; B claims success for its own job
    probe_service.submit_results(b, [{"job_id": j["job_id"], "outcome": "SUCCESS", "http_status": 204} for j in jobs_b])
    assert row(probe_service, creds_b["probe_id"])["verify_reason"] == "nonce_not_received"
    assert not probe_service.nonce_hit("0" * 32, "1.1.1.1")  # unknown nonce
    assert not probe_service.nonce_hit("../../etc", "1.1.1.1")


def test_wrong_status_means_something_in_between_answered(probe_service):
    _, creds, probe = register(probe_service)
    run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1"))
    jobs, job = verification_jobs(probe_service, probe)
    fetch_nonce(job)
    probe_service.submit_results(probe, [{"job_id": j["job_id"], "outcome": "SUCCESS", "http_status": 200} for j in jobs])
    assert row(probe_service, creds["probe_id"])["verify_reason"] == "unexpected_response"


def test_api_unreachable_and_probe_errors(probe_service):
    for outcome, reason in (("TIMEOUT", "api_unreachable"), ("FAILED", "api_unreachable"), ("ERROR", "probe_error")):
        _, creds, probe = register(probe_service, name=f"ISP {outcome}")
        run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1"))
        jobs, _ = verification_jobs(probe_service, probe)
        probe_service.submit_results(probe, [{"job_id": j["job_id"], "outcome": outcome} for j in jobs])
        assert row(probe_service, creds["probe_id"])["verify_reason"] == reason


def test_deadline_reasons(probe_service):
    _, creds, probe = register(probe_service)
    run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1"))  # online, never polls
    probe_service.clock_.advance(config.PROBE_VERIFY_TIMEOUT_SECONDS + 1)
    probe_service.maintain()
    assert row(probe_service, creds["probe_id"])["verify_reason"] == "jobs_not_fetched"

    _, creds2, probe2 = register(probe_service, name="Second")
    run(probe_service.heartbeat(probe2, agent_version="1.0.0", public_ip="1"))
    run(probe_service.lease_jobs(probe2, limit=50))  # fetched, never answered
    probe_service.clock_.advance(config.PROBE_VERIFY_TIMEOUT_SECONDS + 1)
    probe_service.maintain()
    assert row(probe_service, creds2["probe_id"])["verify_reason"] == "no_result"

    _, creds3, _ = register(probe_service, name="Third")  # registered, then silent
    run(probe_service.start_verification(creds3["probe_id"], "admin"))
    probe_service.clock_.advance(config.PROBE_VERIFY_TIMEOUT_SECONDS + 1)
    probe_service.maintain()
    assert row(probe_service, creds3["probe_id"])["verify_reason"] == "never_connected"


def test_failed_verification_retries_automatically_then_stops(probe_service):
    _, creds, probe = register(probe_service)
    for attempt in range(config.PROBE_VERIFY_MAX_ATTEMPTS):
        run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1"))
        assert row(probe_service, creds["probe_id"])["verify_state"] == "running", attempt
        jobs, _ = verification_jobs(probe_service, probe)
        probe_service.submit_results(probe, [{"job_id": j["job_id"], "outcome": "TIMEOUT"} for j in jobs])
        probe = row(probe_service, creds["probe_id"])
        run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1"))  # too soon: no retry
        assert row(probe_service, creds["probe_id"])["verify_state"] == "failed"
        probe_service.clock_.advance(config.PROBE_VERIFY_RETRY_SECONDS + 1)
        probe = row(probe_service, creds["probe_id"])
    run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1"))
    after = row(probe_service, creds["probe_id"])
    assert after["verify_state"] == "failed" and after["verify_attempts"] == config.PROBE_VERIFY_MAX_ATTEMPTS
    # An admin can always run it again.
    assert run(probe_service.start_verification(creds["probe_id"], "admin"))
    assert row(probe_service, creds["probe_id"])["verify_state"] == "running"


def test_without_a_public_url_verification_fails_loudly(probe_service, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_API_URL", "")
    _, creds, probe = register(probe_service)
    run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1"))
    assert row(probe_service, creds["probe_id"])["verify_reason"] == "server_misconfigured"
    errors = probe_service.store.all("SELECT * FROM events WHERE severity='error'")
    assert errors and "PUBLIC_API_URL" in errors[0]["message"]


def test_auto_approve_skips_verification(probe_service, monkeypatch):
    monkeypatch.setattr(config, "PROBE_AUTO_APPROVE", True)
    _, creds, probe = register(probe_service)
    assert (creds["status"], row(probe_service, creds["probe_id"])["verify_state"]) == ("approved", "skipped")
    run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1"))
    assert probe_service.store.one("SELECT COUNT(*) AS n FROM verifications")["n"] == 0


def test_failed_rerun_takes_an_approved_probe_out_of_scoring(probe_service):
    _, creds, probe = register(probe_service)
    run(probe_service.set_status(creds["probe_id"], "approved"))
    run(probe_service.start_verification(creds["probe_id"], "admin"))
    assert row(probe_service, creds["probe_id"])["status"] == "approved"  # still working while it runs
    jobs, _ = verification_jobs(probe_service, probe)
    probe_service.submit_results(probe, [{"job_id": j["job_id"], "outcome": "SUCCESS", "http_status": 204} for j in jobs])
    assert row(probe_service, creds["probe_id"])["status"] == "pending"


def test_spot_check_catches_an_approved_probe_that_invents_results(probe_service, monkeypatch):
    monkeypatch.setattr(config, "PROBE_SPOT_CHECK_SECONDS", 60)
    _, creds, probe = register(probe_service)
    run(probe_service.set_status(creds["probe_id"], "approved"))
    probe_service.clock_.advance(61)
    entry = {"created_at": "x", "ranked": [public_node_at(0, "93.184.215.14")], "top": []}
    run(probe_service.schedule_scan(entry))
    probe = row(probe_service, creds["probe_id"])
    jobs = run(probe_service.lease_jobs(probe, limit=50))
    assert any(j.get("path", "").startswith(VERIFY_PATH) for j in jobs)  # hidden among the work
    probe_service.submit_results(probe, [{"job_id": j["job_id"], "outcome": "SUCCESS", "http_status": 204} for j in jobs])
    after = row(probe_service, creds["probe_id"])
    assert (after["status"], after["verify_reason"]) == ("pending", "spot_check_failed")
    assert probe_service.store.one("SELECT MIN(inconclusive) AS m FROM results WHERE probe_id=?", (probe["id"],))["m"] == 1
    assert probe_service.confirmations([entry["ranked"][0]["fingerprint"]])[entry["ranked"][0]["fingerprint"]]["score"] == 0


def test_spot_check_passes_an_honest_probe(probe_service, monkeypatch):
    monkeypatch.setattr(config, "PROBE_SPOT_CHECK_SECONDS", 60)
    _, creds, probe = register(probe_service)
    run(probe_service.set_status(creds["probe_id"], "approved"))
    probe_service.clock_.advance(61)
    run(probe_service.schedule_scan({"created_at": "x", "ranked": [public_node_at(0, "93.184.215.14")], "top": []}))
    probe = row(probe_service, creds["probe_id"])
    jobs = run(probe_service.lease_jobs(probe, limit=50))
    for job in jobs:
        if job.get("path", "").startswith(VERIFY_PATH):
            fetch_nonce(job)
    probe_service.submit_results(probe, [{"job_id": j["job_id"], "outcome": "SUCCESS", "http_status": 204} for j in jobs])
    assert row(probe_service, creds["probe_id"])["status"] == "approved"


def test_verify_endpoint_reveals_nothing(probe_service):
    client = TestClient(app)
    for path in ("/api/probes/verify/" + "a" * 32, "/api/probes/verify/zzz"):
        reply = client.get(path)
        assert reply.status_code == 204 and reply.content == b""


def test_enrollment_status_walks_through_the_lifecycle_over_http(probe_service):
    client = TestClient(app)
    created = client.post("/api/providers", json={"name": "МТС", "country": "RU"}).json()
    body = {"enrollment_id": created["enrollment_id"], "token": created["token"]}
    creds = client.post("/api/probes/register", json={"token": created["token"], "agent_version": "1.0.0",
                                                      "profile": "linux-mipsle", "platform": "linux/mipsle"}).json()
    auth = {"Authorization": f"Bearer {creds['probe_id']}.{creds['secret']}"}
    assert client.post("/api/providers/enrollment", json=body).json()["state"] == "registered"
    client.post("/api/probes/heartbeat", json={"agent_version": "1.0.0"}, headers=auth)
    assert client.post("/api/providers/enrollment", json=body).json()["state"] == "verifying"
    jobs = client.get("/api/probes/jobs", headers=auth).json()["jobs"]
    nonce_job = next(j for j in jobs if j["path"].startswith(VERIFY_PATH))
    client.get(nonce_job["path"])
    client.post("/api/probes/results", headers=auth,
                json={"results": [{"job_id": j["job_id"], "outcome": "SUCCESS", "http_status": 204} for j in jobs]})
    status = client.post("/api/providers/enrollment", json=body).json()
    assert status["state"] == "verified" and status["probe"]["status"] == "approved"
    assert status["verification"]["state"] == "passed" and status["verification"]["online"] is True
