"""Probe-confirmation scores and the final ranking, through the whole aggregation path:
jobs → results → ProbeService.confirmations → ranking.final_top → cache → public API."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import ranking
from app.services.scan_service import ALL
from tests.conftest import public_node_at

PROVIDERS = ["МТС", "Билайн", "МегаФон", "T2"]
# The task's dataset: which providers' networks reach each config.
REACHES = {
    "A": {"МТС", "Билайн", "МегаФон", "T2"}, "B": {"МТС"}, "C": {"МТС", "Билайн", "МегаФон"},
    "D": {"МТС", "Билайн", "МегаФон", "T2"}, "E": {"Билайн", "МегаФон"}, "F": {"МТС", "Билайн", "МегаФон", "T2"},
    "G": {"Билайн", "МегаФон", "T2"}, "H": {"T2"},
}
NODE_IPS = {name: f"93.184.{10 + i}.10" for i, name in enumerate("ABCDEFGH")}


def run(coro):
    return asyncio.run(coro)


def approved_probe(svc, provider: str, public_ip: str, *, status: str = "approved", online: bool = True):
    enrollment = svc.create_enrollment(name=provider, country="RU")
    creds = svc.register(enrollment["token"], agent_version="1.0.0", profile="linux-amd64",
                         platform="linux/amd64", public_ip=public_ip)
    svc.store.run("UPDATE probes SET status=?, verify_state='passed' WHERE id=?", (status, creds["probe_id"]))
    probe = svc.authenticate(f"Bearer {creds['probe_id']}.{creds['secret']}") if status != "disabled" else \
        svc.store.one("SELECT * FROM probes WHERE id=?", (creds["probe_id"],))
    if online and status != "disabled":
        run(svc.heartbeat(probe, agent_version="1.0.0", public_ip=public_ip))
        probe = svc.store.one("SELECT * FROM probes WHERE id=?", (creds["probe_id"],))
    return probe


def entry_for(order: str) -> dict:
    """A cached scan whose usable candidates are in `order` (the backend's ranking)."""
    nodes = []
    for i, name in enumerate(order):
        node = public_node_at(i, NODE_IPS[name], latency=50 + i)
        node.update(fingerprint=f"fp-{name}", name=name)
        nodes.append(node)
    return {"created_at": "2026-10-01T00:00:00Z", "top": nodes[:5], "ranked": nodes, "total_nodes": 8, "tested": 8,
            "working": 8, "sources": [], "duration_seconds": 1.0}


def answer(svc, probe, reaches_by_ip: dict[str, bool], outcome_blocked="FAILED"):
    jobs = run(svc.lease_jobs(probe, limit=50))
    results = []
    for job in jobs:
        if job["type"] == "HTTP_GET":
            results.append({"job_id": job["job_id"], "outcome": "SUCCESS", "http_status": 204})
        else:
            ok = reaches_by_ip[job["host"]]
            results.append({"job_id": job["job_id"], "outcome": "SUCCESS" if ok else outcome_blocked,
                            "latency_ms": 40.0 if ok else None})
    return svc.submit_results(probe, results)


def build_scenario(svc, order="HGFEDCBA", probes_per_provider=2):
    entry = entry_for(order)
    run(svc.schedule_scan(entry))
    probes = []
    for p_index, provider in enumerate(PROVIDERS):
        for i in range(probes_per_provider):
            probes.append((provider, approved_probe(svc, provider, f"198.51.{p_index}.{10 + i}")))
    for provider, probe in probes:
        reaches = {NODE_IPS[n]: provider in REACHES[n] for n in "ABCDEFGH"}
        answer(svc, probe, reaches)
    return entry, probes


# --- scores ------------------------------------------------------------------------------
def test_scores_equal_the_number_of_confirming_providers(probe_service):
    entry, _ = build_scenario(probe_service)
    conf = probe_service.confirmations([f"fp-{n}" for n in "ABCDEFGH"])
    assert {fp[3:]: c["score"] for fp, c in conf.items()} == {"A": 4, "B": 1, "C": 3, "D": 4, "E": 2, "F": 4, "G": 3,
                                                              "H": 1}
    assert all(c["total"] == 4 for c in conf.values())
    assert conf["fp-E"]["providers"] == sorted(["Билайн", "МегаФон"])


@pytest.mark.parametrize("confirming", [1, 2, 3, 4])
def test_n_of_4_confirmations_give_n_points(probe_service, confirming):
    entry = entry_for("A")
    run(probe_service.schedule_scan(entry))
    for i, provider in enumerate(PROVIDERS):
        probe = approved_probe(probe_service, provider, f"198.51.{i}.1")
        answer(probe_service, probe, {NODE_IPS["A"]: i < confirming})
    assert probe_service.confirmations(["fp-A"])["fp-A"] == {
        "score": confirming, "total": 4, "providers": sorted(PROVIDERS[:confirming])}


# --- ranking over the full path ------------------------------------------------------------
def test_top5_prefers_maximum_scores_whatever_the_backend_order(probe_service):
    entry, _ = build_scenario(probe_service, order="HGFEDCBA")  # backend: H fastest, A slowest
    top = ranking.final_top(entry, probe_service.confirmations)
    assert [n["name"] for n in top] == ["F", "D", "A", "G", "C"]  # 4,4,4 then 3,3; ties in backend order
    assert [n["probe_score"] for n in top] == [4, 4, 4, 3, 3]
    assert [n["backend_rank"] for n in top] == [3, 5, 8, 2, 6]


def test_public_api_serves_the_probe_aware_ranking(probe_service, api_service):
    entry, _ = build_scenario(probe_service, order="ABCDEFGH")
    api_service._slot(ALL).cache.save(entry)  # what a finished scan writes
    client = TestClient(app)
    nodes = client.get("/api/results").json()["nodes"]
    assert [n["label"] for n in nodes] == ["A", "D", "F", "C", "G"]
    assert [(n["rank"], n["probe_score"], n["probe_total"]) for n in nodes] == \
        [(1, 4, 4), (2, 4, 4), (3, 4, 4), (4, 3, 4), (5, 3, 4)]
    assert [n["name"] for n in client.get("/api/vless/top").json()["nodes"]] == ["A", "D", "F", "C", "G"]
    links = {node["name"]: node["link"] for node in entry["ranked"]}
    assert client.get("/api/vless/raw").text.splitlines() == [links[n] for n in "ADFCG"]
    # limit never goes past the top, as before.
    assert len(client.get("/api/vless/top", params={"limit": 50}).json()["nodes"]) == config.TOP_COUNT


def test_no_probe_results_keep_the_backend_order(probe_service):
    entry = entry_for("HGFEDCBA")
    top = ranking.final_top(entry, probe_service.confirmations)
    assert [n["name"] for n in top] == list("HGFED")
    assert all(n["probe_score"] == 0 for n in top)


def test_score_can_be_turned_off(probe_service, monkeypatch):
    entry, _ = build_scenario(probe_service, order="HGFEDCBA")
    monkeypatch.setattr(config, "RANKING_USE_PROBE_SCORE", False)
    top = ranking.final_top(entry, probe_service.confirmations)
    assert [n["name"] for n in top] == list("HGFED")  # backend order, scores still reported
    assert [n["probe_score"] for n in top] == [1, 3, 4, 2, 4]


def test_probes_cannot_promote_a_config_the_backend_found_unusable(probe_service):
    entry, _ = build_scenario(probe_service, order="HGFEDCBA")
    for node in entry["ranked"]:
        if node["name"] == "F":
            node["speed_mbps"] = None  # its download never completed
    top = ranking.final_top(entry, probe_service.confirmations)
    assert "F" not in [n["name"] for n in top]
    assert [n["name"] for n in top] == ["D", "A", "G", "C", "E"]


def test_broken_probe_data_falls_back_to_the_backend_ranking(probe_service):
    entry = entry_for("HGFEDCBA")

    def broken(_):
        raise RuntimeError("database locked")

    assert [n["name"] for n in ranking.final_top(entry, broken)] == list("HGFED")


# --- duplicates and identity -----------------------------------------------------------------
def test_one_probe_answering_again_never_adds_points(probe_service):
    entry = entry_for("A")
    run(probe_service.schedule_scan(entry))
    probe = approved_probe(probe_service, "МТС", "198.51.0.1")
    jobs = run(probe_service.lease_jobs(probe, limit=50))
    results = [{"job_id": j["job_id"], "outcome": "SUCCESS"} for j in jobs]
    assert probe_service.submit_results(probe, results)["accepted"] == len(jobs)
    replay = probe_service.submit_results(probe, results * 3)  # replayed and duplicated
    assert replay["accepted"] == 0 and len(replay["rejected"]) == 3 * len(jobs)
    # A fresh round after the TTL: a second result row for the same probe and target.
    probe_service.clock_.advance(config.PROBE_RESULT_TTL_SECONDS + 1)
    run(probe_service.heartbeat(probe, agent_version="1.0.0", public_ip="198.51.0.1"))
    answer(probe_service, probe, {NODE_IPS["A"]: True})
    assert probe_service.store.one("SELECT COUNT(*) AS n FROM results WHERE probe_id=?", (probe["id"],))["n"] == 2
    assert probe_service.confirmations(["fp-A"])["fp-A"]["score"] == 1


def test_only_the_latest_answer_of_a_probe_counts(probe_service):
    entry = entry_for("A")
    run(probe_service.schedule_scan(entry))
    probe = approved_probe(probe_service, "МТС", "198.51.0.1")
    answer(probe_service, probe, {NODE_IPS["A"]: True})
    probe_service.clock_.advance(config.PROBE_RESULT_TTL_SECONDS - 60)
    probe_service.store.run("UPDATE results SET expires_at=? WHERE probe_id=?",  # the job is re-run early
                            (probe_service.clock_.now - 1, probe["id"]))
    run(probe_service.ensure_jobs_for_probe(probe["id"]))
    answer(probe_service, probe, {NODE_IPS["A"]: False})
    assert probe_service.confirmations(["fp-A"])["fp-A"] == {"score": 0, "total": 1, "providers": []}


def test_many_probes_of_one_provider_give_one_point(probe_service):
    entry = entry_for("A")
    run(probe_service.schedule_scan(entry))
    for i in range(5):
        answer(probe_service, approved_probe(probe_service, "МТС", f"198.51.0.{i + 1}"), {NODE_IPS["A"]: True})
    assert probe_service.confirmations(["fp-A"])["fp-A"] == {"score": 1, "total": 1, "providers": ["МТС"]}


def test_one_connection_cannot_pose_as_several_providers(probe_service):
    entry = entry_for("A")
    run(probe_service.schedule_scan(entry))
    for name in ("ISP one", "ISP two", "ISP three"):
        answer(probe_service, approved_probe(probe_service, name, "198.51.0.99"), {NODE_IPS["A"]: True})
    assert probe_service.confirmations(["fp-A"])["fp-A"]["score"] == 1
    # A provider with a second, distinct address can still be matched.
    answer(probe_service, approved_probe(probe_service, "ISP two", "198.51.7.7"), {NODE_IPS["A"]: True})
    assert probe_service.confirmations(["fp-A"])["fp-A"]["score"] == 2


# --- probe states -------------------------------------------------------------------------------
def test_pending_disabled_and_failed_probes_never_contribute(probe_service):
    entry = entry_for("A")
    run(probe_service.schedule_scan(entry))
    good = approved_probe(probe_service, "МТС", "198.51.0.1")
    answer(probe_service, good, {NODE_IPS["A"]: True})
    pending = approved_probe(probe_service, "Билайн", "198.51.1.1", status="pending")
    assert [j for j in run(probe_service.lease_jobs(pending, limit=50)) if j["type"] != "HTTP_GET"] == []
    # Even a result forced into the table for a pending probe is ignored.
    probe_service.store.run(
        "INSERT INTO results (probe_id, provider_id, key, type, ip, port, outcome, inconclusive, checked_at, expires_at)"
        " SELECT ?, ?, key, 'TLS_CONNECT', '1.1.1.1', 443, 'SUCCESS', 0, ?, ? FROM targets",
        (pending["id"], pending["provider_id"], probe_service.clock_.now, probe_service.clock_.now + 3600))
    to_disable = approved_probe(probe_service, "МегаФон", "198.51.2.1")
    answer(probe_service, to_disable, {NODE_IPS["A"]: True})
    assert probe_service.confirmations(["fp-A"])["fp-A"]["score"] == 2
    run(probe_service.set_status(to_disable["id"], "disabled"))
    assert probe_service.confirmations(["fp-A"])["fp-A"] == {"score": 1, "total": 1, "providers": ["МТС"]}


def test_offline_probe_results_count_until_they_expire(probe_service):
    entry = entry_for("A")
    run(probe_service.schedule_scan(entry))
    probe = approved_probe(probe_service, "МТС", "198.51.0.1")
    answer(probe_service, probe, {NODE_IPS["A"]: True})
    probe_service.clock_.advance(config.PROBE_OFFLINE_AFTER_SECONDS + 1)  # offline now
    assert probe_service.confirmations(["fp-A"])["fp-A"]["score"] == 1  # a real, recent observation
    probe_service.clock_.advance(config.PROBE_RESULT_TTL_SECONDS)
    assert probe_service.confirmations(["fp-A"])["fp-A"] == {"score": 0, "total": 0, "providers": []}


def test_offline_probes_get_no_new_work(probe_service):
    probe = approved_probe(probe_service, "МТС", "198.51.0.1")
    probe_service.clock_.advance(config.PROBE_OFFLINE_AFTER_SECONDS + 1)
    summary = run(probe_service.schedule_scan(entry_for("AB")))
    assert summary["jobs_created"] == 0 and summary["probes"] == 0


def test_inconclusive_failures_do_not_count_as_answers(probe_service):
    entry = entry_for("A")
    run(probe_service.schedule_scan(entry))
    probe = approved_probe(probe_service, "МТС", "198.51.0.1")
    jobs = run(probe_service.lease_jobs(probe, limit=50))
    # Its own internet is down (control FAILED): the endpoint failure says nothing.
    probe_service.submit_results(probe, [{"job_id": j["job_id"], "outcome": "FAILED"} for j in jobs])
    assert probe_service.confirmations(["fp-A"])["fp-A"] == {"score": 0, "total": 0, "providers": []}


def test_results_are_tied_to_the_right_config(probe_service):
    entry = entry_for("AB")
    run(probe_service.schedule_scan(entry))
    probe = approved_probe(probe_service, "МТС", "198.51.0.1")
    answer(probe_service, probe, {NODE_IPS["A"]: True, NODE_IPS["B"]: False})
    conf = probe_service.confirmations(["fp-A", "fp-B", "fp-unknown"])
    assert (conf["fp-A"]["score"], conf["fp-B"]["score"], conf["fp-unknown"]["score"]) == (1, 0, 0)
    assert conf["fp-B"]["total"] == 1
