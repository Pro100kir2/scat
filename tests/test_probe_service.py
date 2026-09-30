"""ProbeService: enrollment → registration → heartbeats → jobs → results → per-provider states."""
import asyncio

import pytest

from app import config
from app.probes.service import EnrollmentError, ProbeAuthError, ProbeForbidden
from tests.conftest import public_node_at

IPS = ["93.184.215.14", "1.1.1.1", "8.8.8.8", "9.9.9.9", "104.16.1.1", "151.101.1.1"]


def run(coro):
    return asyncio.run(coro)


def add_probe(svc, name="МТС", country="RU", region="Москва", approve=True, online=True):
    enrollment = svc.create_enrollment(name=name, country=country, region=region, asn=8359)
    creds = svc.register(enrollment["token"], agent_version="1.0.0", profile="linux-mipsle",
                         platform="linux/mipsle", public_ip="203.0.114.7")
    if approve:
        svc.store.run("UPDATE probes SET status='approved' WHERE id=?", (creds["probe_id"],))
    probe = svc.authenticate(f"Bearer {creds['probe_id']}.{creds['secret']}")
    if online:
        run(svc.heartbeat(probe, agent_version="1.0.0", public_ip="203.0.114.7"))
        probe = svc.authenticate(f"Bearer {creds['probe_id']}.{creds['secret']}")
    return probe, creds


def scan_entry(n=25, security="reality"):
    nodes = [public_node_at(i, IPS[i % len(IPS)] if i < len(IPS) else f"93.184.{i}.10", security=security)
             for i in range(n)]
    return {"created_at": "2026-09-30T10:00:00Z", "top": nodes[:5], "ranked": nodes}


def answer_all(svc, probe, outcome="SUCCESS", control="SUCCESS", latency=42.0):
    jobs = run(svc.lease_jobs(probe, limit=50))
    results = []
    for job in jobs:
        is_control = job["type"] == "HTTP_GET"
        results.append({"job_id": job["job_id"], "outcome": control if is_control else outcome,
                        "latency_ms": latency})
    return jobs, svc.submit_results(probe, results)


# --- enrollment & registration -----------------------------------------------------------
def test_enrollment_creates_provider_once_and_one_time_tokens(probe_service):
    a = probe_service.create_enrollment(name="МТС", country="RU")
    b = probe_service.create_enrollment(name="  мтс ", country="RU")
    c = probe_service.create_enrollment(name="МТС", country="BY")
    assert a["provider"]["id"] == b["provider"]["id"] != c["provider"]["id"]
    assert a["token"] != b["token"] and a["token"].startswith("skenr_")
    stored = probe_service.store.one("SELECT token_hash FROM enrollments WHERE id=?", (a["enrollment_id"],))
    assert a["token"] not in stored["token_hash"]  # only a hash is stored

    creds = probe_service.register(a["token"], agent_version="1.0.0", profile="linux-amd64",
                                   platform="linux/amd64", public_ip="203.0.114.7")
    assert creds["probe_id"].startswith("prb_") and creds["status"] == "pending"
    with pytest.raises(EnrollmentError):  # one probe per token
        probe_service.register(a["token"], agent_version="1.0.0", profile="linux-amd64",
                               platform="linux/amd64", public_ip="203.0.114.7")


def test_expired_or_forged_enrollment_token(probe_service):
    e = probe_service.create_enrollment(name="Beeline", country="RU")
    with pytest.raises(EnrollmentError):
        probe_service.register("skenr_forged", agent_version="1.0.0", profile="linux-amd64",
                               platform="linux/amd64", public_ip="1.2.3.4")
    probe_service.clock_.advance(config.PROBE_ENROLLMENT_TTL_SECONDS + 1)
    with pytest.raises(EnrollmentError):
        probe_service.register(e["token"], agent_version="1.0.0", profile="linux-amd64",
                               platform="linux/amd64", public_ip="1.2.3.4")


def test_enrollment_status_follows_the_installation(probe_service):
    e = probe_service.create_enrollment(name="T2", country="RU")
    status = lambda: probe_service.enrollment_status(e["enrollment_id"], e["token"])  # noqa: E731
    assert status()["state"] == "waiting"
    probe_service.diagnose({"os": "Darwin"}, token=e["token"])
    assert status()["state"] == "unsupported"
    assert status()["diagnostics"]["reasons"][0]["code"] == "os_unsupported"
    probe_service.diagnose({"os": "Linux", "machine": "x86_64", "elf_class": 64, "kernel": "6.1", "mem_total_kb": 10**6,
                            "mem_available_kb": 10**6, "storage_free_kb": 10**7, "curl": True, "is_root": True,
                            "ipv4": True, "init": "systemd"}, token=e["token"])
    assert status()["state"] == "installing"
    creds = probe_service.register(e["token"], agent_version="1.0.0", profile="linux-amd64",
                                   platform="linux/amd64", public_ip="1.2.3.4")
    assert status()["state"] == "registered"
    probe = probe_service.authenticate(f"Bearer {creds['probe_id']}.{creds['secret']}")
    run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1.2.3.4"))
    # First contact starts automatic verification; the page says so instead of an error.
    assert status()["state"] == "verifying"
    assert status()["verification"]["state"] == "running" and status()["verification"]["online"] is True
    assert "public_ip" not in status()["probe"]  # the volunteer's IP never leaves the server
    probe_service.clock_.advance(config.PROBE_OFFLINE_AFTER_SECONDS + 1)
    assert status()["verification"]["online"] is False
    with pytest.raises(EnrollmentError):
        probe_service.enrollment_status(e["enrollment_id"], "skenr_wrong")


def test_diagnostics_return_checksum_for_supported_devices(probe_service):
    result = probe_service.diagnose({"os": "Linux", "machine": "mips", "elf_class": 32, "openwrt": True,
                                     "openwrt_arch": "mipsel_24kc", "kernel": "5.15", "mem_total_kb": 250_000,
                                     "mem_available_kb": 150_000, "storage_free_kb": 60_000, "wget_https": True,
                                     "is_root": True, "ipv4": True, "init": "procd"})
    assert result["verdict"] == "SUPPORTED" and result["profile"] == "linux-mipsle"
    assert len(result["release"]["sha256"]) == 64 and result["release"]["size"] > 0
    unsupported = probe_service.diagnose({"os": "Linux", "machine": "sparc64", "kernel": "6.1"})
    assert unsupported["verdict"] == "UNSUPPORTED" and unsupported["release"] is None


# --- authentication ---------------------------------------------------------------------------
def test_authentication_and_rotation(probe_service):
    probe, creds = add_probe(probe_service)
    good = f"Bearer {creds['probe_id']}.{creds['secret']}"
    for bad in ("", "Bearer", f"Basic {creds['probe_id']}.{creds['secret']}", f"Bearer {creds['probe_id']}.nope",
                "Bearer prb_0000000000000000.x", f"Bearer {creds['probe_id']}"):
        with pytest.raises(ProbeAuthError):
            probe_service.authenticate(bad)
    stored = probe_service.store.one("SELECT secret_hash FROM probes WHERE id=?", (creds["probe_id"],))
    assert creds["secret"] not in stored["secret_hash"]

    new = probe_service.rotate(probe)
    assert probe_service.authenticate(f"Bearer {creds['probe_id']}.{new['secret']}")
    assert probe_service.authenticate(good)  # grace period
    probe_service.clock_.advance(3601)
    with pytest.raises(ProbeAuthError):
        probe_service.authenticate(good)

    probe_service.store.run("UPDATE probes SET status='disabled' WHERE id=?", (creds["probe_id"],))
    with pytest.raises(ProbeForbidden):
        probe_service.authenticate(f"Bearer {creds['probe_id']}.{new['secret']}")


def test_heartbeat_asks_for_rotation_when_credentials_age(probe_service):
    probe, _ = add_probe(probe_service)
    assert run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1.2.3.4"))["rotate_credentials"] is False
    probe_service.clock_.advance(config.PROBE_CREDENTIAL_MAX_AGE_SECONDS + 1)
    assert run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1.2.3.4"))["rotate_credentials"] is True


# --- offline --------------------------------------------------------------------------------
def test_probe_goes_offline_after_missed_heartbeats(probe_service):
    probe, creds = add_probe(probe_service)
    assert probe_service.providers_overview()["providers"][0]["status"] == "online"
    probe_service.clock_.advance(config.PROBE_HEARTBEAT_SECONDS * 3)
    assert probe_service.providers_overview()["providers"][0]["status"] == "online"  # 3 missed: still in grace
    probe_service.clock_.advance(config.PROBE_OFFLINE_AFTER_SECONDS)
    overview = probe_service.providers_overview()
    assert overview["providers"][0]["status"] == "offline"
    assert overview["probes"][0]["online"] is False and overview["probes"][0]["last_seen"]


# --- jobs ------------------------------------------------------------------------------------
def test_scan_round_creates_deduplicated_jobs_for_top_25(probe_service):
    probe, _ = add_probe(probe_service)
    entry = scan_entry(30)
    entry["ranked"][7]["address"] = entry["ranked"][0]["address"]  # same endpoint twice
    entry["ranked"][7]["sni"] = entry["ranked"][0]["sni"]
    summary = run(probe_service.schedule_scan(entry))
    assert summary["targets"] == config.PROBE_TARGET_COUNT == 25  # the 30th config is not checked
    jobs = run(probe_service.lease_jobs(probe, limit=50))
    types = [j["type"] for j in jobs]
    assert types[0] == "HTTP_GET"  # control check first
    assert len(jobs) == config.PROBE_JOBS_PER_POLL  # 1 control + 24 unique targets, 10 per poll
    assert probe_service.store.one("SELECT COUNT(*) AS n FROM jobs")["n"] == 1 + 24
    # Everything a job contains is typed, validated data; hosts are IP literals.
    for job in jobs:
        assert set(job) <= {"job_id", "type", "host", "port", "timeout_ms", "server_name", "path", "tls"}
        assert job["host"].replace(".", "").isdigit()


def test_rescheduling_does_not_duplicate_open_jobs(probe_service):
    probe, _ = add_probe(probe_service)
    entry = scan_entry(5)
    first = run(probe_service.schedule_scan(entry))["jobs_created"]
    second = run(probe_service.schedule_scan(entry))["jobs_created"]
    assert first == 6 and second == 0  # 5 targets + control, then nothing new
    count = probe_service.store.one("SELECT COUNT(*) AS n FROM jobs")["n"]
    assert count == 6


def test_results_are_cached_and_reused_across_rounds(probe_service):
    probe, _ = add_probe(probe_service)
    run(probe_service.schedule_scan(scan_entry(5)))
    answer_all(probe_service, probe)
    assert run(probe_service.schedule_scan(scan_entry(5)))["jobs_created"] == 0  # fresh results reused
    probe_service.clock_.advance(config.PROBE_RESULT_TTL_SECONDS + 1)
    # Back after a day: the heartbeat itself re-queues the expired targets (and the control).
    run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1.2.3.4"))
    assert len(run(probe_service.lease_jobs(probe, limit=50))) == 6
    assert run(probe_service.schedule_scan(scan_entry(5)))["jobs_created"] == 0  # already open


def test_offline_probes_get_no_jobs_until_they_return(probe_service):
    probe, _ = add_probe(probe_service)
    probe_service.clock_.advance(config.PROBE_OFFLINE_AFTER_SECONDS + 1)
    assert run(probe_service.schedule_scan(scan_entry(5)))["jobs_created"] == 0
    run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="1.2.3.4"))  # back online
    assert len(run(probe_service.lease_jobs(probe, limit=50))) == 6


def test_returning_probe_gets_jobs_even_if_its_poll_beats_the_heartbeat(probe_service):
    probe, _ = add_probe(probe_service)
    run(probe_service.schedule_scan(scan_entry(3)))
    answer_all(probe_service, probe)
    probe_service.clock_.advance(config.PROBE_RESULT_TTL_SECONDS + 1)  # offline, results expired
    stale = probe_service.store.one("SELECT * FROM probes WHERE id=?", (probe["id"],))
    # The agent's job poll and heartbeat race; here the poll wins.
    assert len(run(probe_service.lease_jobs(stale, limit=50))) == 4
    run(probe_service.heartbeat(stale, agent_version="1.0.0", public_ip="1.2.3.4"))
    assert run(probe_service.lease_jobs(stale, limit=50)) == []  # no duplicates from the heartbeat


def test_lease_timeout_retries_then_expires(probe_service):
    probe, _ = add_probe(probe_service)
    run(probe_service.schedule_scan(scan_entry(1)))
    assert len(run(probe_service.lease_jobs(probe))) == 2
    assert run(probe_service.lease_jobs(probe)) == []  # leased jobs are not handed out twice
    probe_service.clock_.advance(60)  # lease = 5 s timeout + 30 s grace
    assert probe_service.maintain()["retried"] == 2
    assert len(run(probe_service.lease_jobs(probe))) == 2  # attempt 2
    probe_service.clock_.advance(60)
    assert probe_service.maintain() == {"retried": 0, "expired": 2}  # PROBE_JOB_MAX_ATTEMPTS = 2
    assert run(probe_service.lease_jobs(probe)) == []


def test_unclaimed_jobs_expire(probe_service):
    add_probe(probe_service)
    run(probe_service.schedule_scan(scan_entry(3)))
    probe_service.clock_.advance(config.PROBE_JOB_TTL_SECONDS + 1)
    assert probe_service.maintain()["expired"] == 4


def test_long_poll_wakes_up_when_jobs_arrive(probe_service):
    probe, _ = add_probe(probe_service)

    async def scenario():
        waiter = asyncio.create_task(probe_service.lease_jobs(probe, wait_seconds=5))
        await asyncio.sleep(0.05)
        await probe_service.schedule_scan(scan_entry(2))
        return await asyncio.wait_for(waiter, 2)

    assert len(run(scenario())) == 3


# --- results ----------------------------------------------------------------------------------
def test_results_only_for_own_open_jobs(probe_service):
    probe_a, _ = add_probe(probe_service, name="МТС")
    probe_b, _ = add_probe(probe_service, name="Билайн")
    run(probe_service.schedule_scan(scan_entry(1)))
    job_a = run(probe_service.lease_jobs(probe_a))[0]
    # B cannot answer A's job, and nobody can answer a job twice or invent one.
    assert probe_service.submit_results(probe_b, [{"job_id": job_a["job_id"], "outcome": "SUCCESS"}])["accepted"] == 0
    assert probe_service.submit_results(probe_a, [{"job_id": job_a["job_id"], "outcome": "SUCCESS"}])["accepted"] == 1
    assert probe_service.submit_results(probe_a, [{"job_id": job_a["job_id"], "outcome": "SUCCESS"}])["accepted"] == 0
    fake = "job_" + "0" * 20
    assert probe_service.submit_results(probe_a, [{"job_id": fake, "outcome": "SUCCESS"}])["rejected"] == [fake]


def test_failed_control_check_makes_failures_inconclusive(probe_service):
    probe, _ = add_probe(probe_service)
    run(probe_service.schedule_scan(scan_entry(3)))
    answer_all(probe_service, probe, outcome="TIMEOUT", control="FAILED")
    fps = [n["fingerprint"] for n in scan_entry(3)["top"]]
    states = {row["state"] for rows in probe_service.reachability(fps).values() for row in rows}
    assert states == {"NOT_TESTED"}  # the probe's own internet was down: no verdict on endpoints


# --- aggregation -------------------------------------------------------------------------------
def test_provider_states_for_top_configs(probe_service):
    mts, _ = add_probe(probe_service, name="МТС")
    beeline, _ = add_probe(probe_service, name="Билайн")
    megafon, _ = add_probe(probe_service, name="МегаФон")
    t2, _ = add_probe(probe_service, name="T2")
    add_probe(probe_service, name="Pending ISP", approve=False)  # not shown anywhere public
    entry = scan_entry(5)
    run(probe_service.schedule_scan(entry))
    answer_all(probe_service, mts, "SUCCESS")
    answer_all(probe_service, beeline, "FAILED")
    answer_all(probe_service, megafon, "TIMEOUT")
    # T2 has leased nothing yet → its jobs are still open.
    fp = entry["top"][0]["fingerprint"]
    rows = {r["provider"]: r for r in probe_service.reachability([fp])[fp]}
    assert set(rows) == {"МТС", "Билайн", "МегаФон", "T2"}
    assert rows["МТС"]["state"] == "SUCCESS" and rows["МТС"]["latency_ms"] == 42
    assert rows["Билайн"]["state"] == "FAILED"
    assert rows["МегаФон"]["state"] == "TIMEOUT"
    assert rows["T2"]["state"] == "CHECKING"

    probe_service.clock_.advance(config.PROBE_OFFLINE_AFTER_SECONDS + 1)
    probe_service.maintain()
    rows = {r["provider"]: r for r in probe_service.reachability([fp])[fp]}
    assert rows["T2"]["state"] == "OFFLINE"
    assert rows["МТС"]["state"] == "SUCCESS"  # results outlive heartbeats until their own TTL
    probe_service.clock_.advance(config.PROBE_RESULT_TTL_SECONDS)
    rows = {r["provider"]: r for r in probe_service.reachability([fp])[fp]}
    assert rows["МТС"]["state"] == "OFFLINE"  # expired result, probe offline


def test_one_successful_probe_makes_the_provider_reachable(probe_service):
    first, _ = add_probe(probe_service, name="МТС", region="Москва")
    second, _ = add_probe(probe_service, name="МТС", region="Казань")
    entry = scan_entry(1)
    run(probe_service.schedule_scan(entry))
    answer_all(probe_service, first, "FAILED")
    answer_all(probe_service, second, "SUCCESS")
    fp = entry["top"][0]["fingerprint"]
    (row,) = probe_service.reachability([fp])[fp]
    assert row["state"] == "SUCCESS" and row["probes"] == 2
    provider = probe_service.providers_overview()["providers"][0]
    assert provider["probes_total"] == 2 and provider["regions"] == ["Казань", "Москва"]
    # Individual results stay stored for finer views later.
    assert probe_service.store.one("SELECT COUNT(DISTINCT probe_id) AS n FROM results")["n"] == 2


def test_disabled_provider_shows_no_probe_and_unknown_configs_not_tested(probe_service):
    probe, creds = add_probe(probe_service, name="МТС")
    entry = scan_entry(2)
    run(probe_service.schedule_scan(entry))
    run(probe_service.set_status(creds["probe_id"], "disabled"))
    fp = entry["top"][0]["fingerprint"]
    assert probe_service.reachability([fp])[fp][0]["state"] == "NO_PROBE"
    assert probe_service.reachability(["never-scanned"])["never-scanned"][0]["state"] == "NO_PROBE"
    probe2, _ = add_probe(probe_service, name="МТС")
    assert probe_service.reachability(["never-scanned"])["never-scanned"][0]["state"] == "NOT_TESTED"


def test_rejected_targets_are_not_tested(probe_service):
    probe, _ = add_probe(probe_service)
    entry = scan_entry(2)
    entry["ranked"][0]["address"] = "192.168.1.1"  # poisoned subscription list
    summary = run(probe_service.schedule_scan(entry))
    assert summary["rejected"] == 1
    jobs = run(probe_service.lease_jobs(probe, limit=50))
    assert all(j["host"] != "192.168.1.1" for j in jobs)
    fp = entry["ranked"][0]["fingerprint"]
    assert probe_service.reachability([fp])[fp][0]["state"] == "NOT_TESTED"


def test_pending_probes_are_private(probe_service):
    add_probe(probe_service, name="Spam ISP", approve=False)
    overview = probe_service.providers_overview()
    assert overview["providers"] == [] and overview["probes"] == []
    assert len(probe_service.admin_probes()) == 1  # admins see it, with its IP
    assert probe_service.admin_probes()[0]["public_ip"] == "203.0.114.7"
