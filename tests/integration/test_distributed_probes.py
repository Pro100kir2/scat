"""25 working probes, end to end, through the real protocol.

Server (uvicorn, real HTTP) → verification jobs → 25 agents (tests/probe_agent.py, the
agent's contract in Python) really fetch the one-time URL → approved → a scan → jobs →
agents really open TLS connections to the candidates' endpoints (the endpoints count the
handshakes) through their providers' simulated networks → results → aggregation →
probe-confirmation scores → the public ranking. Plus two hostile clients that must not
change any score: a liar that answers SUCCESS to everything without running anything, and
a probe that is disabled by the admin after it contributed.

Run alone:  pytest -m integration -v
"""
import asyncio
import time

import httpx
import pytest

from tests.integration.harness import (EXPECTED_SCORES, EXPECTED_TOP5, PROBES_PER_PROVIDER, PROVIDERS, REACHES,
                                       loopback_or_public)
from tests.probe_agent import Checker, ProbeAgent

pytestmark = pytest.mark.integration

TIMEOUT = 90


async def wait_for(predicate, timeout: float = TIMEOUT, interval: float = 0.25, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}")


def network_for(provider: str, port_of: dict, probe_index: int):
    """The provider's ISP: candidates it does not let through are reset (DPI) or, for every
    third probe, silently dropped (the check times out). Everything else passes, including
    the API and the control URL: these probes' own internet works."""
    blocked = {port_of[name] for name, reaches in REACHES.items() if provider not in reaches}
    mode = "blackhole" if probe_index % 3 == 2 else "refused"

    def network(ip, port):
        return mode if port in blocked else None

    return network


async def enroll(client: httpx.AsyncClient, provider: str, phone: str | None = None) -> str:
    body = {"name": provider, "country": "RU", "region": "", "asn": None, "info": ""}
    if phone:
        body["contact_phone"] = phone
    reply = await client.post("/api/providers", json=body)
    assert reply.status_code == 201, reply.text
    return reply.json()["token"]


def test_25_probes_verify_scan_score_and_rank(distributed):
    asyncio.run(scenario(distributed))


async def scenario(env):
    server, admin, port_of, endpoints = env["server"], env["admin"], env["port_of"], env["endpoints"]
    api = httpx.AsyncClient(base_url=server.url, timeout=30)
    agents: list[tuple[str, ProbeAgent]] = []
    tasks: list[asyncio.Task] = []
    try:
        # --- 25 honest probes in four providers' networks ---------------------------------
        for provider in PROVIDERS:
            for i in range(PROBES_PER_PROVIDER[provider]):
                agent = ProbeAgent(server.url, Checker(allowed=loopback_or_public,
                                                       network=network_for(provider, port_of, i)),
                                   delay=2.0 if (provider, i) == ("Билайн", 0) else 0.0,  # one slow device
                                   public_ip=f"198.51.{PROVIDERS.index(provider)}.{10 + i}")
                await agent.register(await enroll(api, provider, phone="+7 912 000-00-%02d" % i if i == 0 else None))
                agents.append((provider, agent))
        assert len(agents) == 25
        # --- hostile: answers SUCCESS to every job without running anything ----------------
        liar = ProbeAgent(server.url, Checker(allowed=loopback_or_public), liar=True, public_ip="203.0.113.66")
        await liar.register(await enroll(api, "Fake ISP"))
        # --- a real probe that reaches everything, disabled by the admin later -------------
        rogue = ProbeAgent(server.url, Checker(allowed=loopback_or_public), public_ip="203.0.113.77")
        await rogue.register(await enroll(api, "Rogue ISP"))

        everyone = [a for _, a in agents] + [liar, rogue]
        tasks = [asyncio.create_task(a.run()) for a in everyone]

        # --- automatic verification: no admin involved --------------------------------------
        async def approved_count():
            data = (await api.get("/api/admin/probes", params={"status": "approved", "per_page": 200},
                                  headers=admin)).json()
            return data["total"] if data["total"] == 26 else 0

        await wait_for(approved_count, what="26 probes to pass verification")
        liar_row = (await api.get(f"/api/admin/probes/{liar.probe_id}", headers=admin)).json()
        await wait_for(lambda: _state(api, admin, liar.probe_id, "failed"), what="the liar's verification to fail")
        liar_row = (await api.get(f"/api/admin/probes/{liar.probe_id}", headers=admin)).json()
        assert liar_row["status"] == "pending"
        assert liar_row["verification"]["reason"] == "nonce_not_received"
        assert liar.checker.executed == []  # it never ran anything

        # --- a scan: the candidates become probe targets --------------------------------------
        assert (await api.post("/api/admin/scanner/run", headers=admin)).json()["outcome"] == "started"

        async def all_answered():
            ranking = (await api.get("/api/admin/cache", headers=admin)).json()["ranking"]
            # 4 honest providers + the rogue one have a conclusive answer for every candidate.
            return ranking if len(ranking) == 8 and all(r["probe_total"] == 5 for r in ranking) else None

        ranking = await wait_for(all_answered, what="every provider to answer every candidate")
        with_rogue = {r["label"]: r["probe_score"] for r in ranking}
        assert with_rogue == {name: score + 1 for name, score in EXPECTED_SCORES.items()}

        # --- the admin disables the rogue probe: its confirmations stop counting at once ------
        assert (await api.post(f"/api/admin/probes/{rogue.probe_id}/disable", headers=admin)).status_code == 200
        ranking = (await api.get("/api/admin/cache", headers=admin)).json()["ranking"]
        assert {r["label"]: r["probe_score"] for r in ranking} == EXPECTED_SCORES
        assert all(r["probe_total"] == 4 for r in ranking)

        # --- the public API serves the probe-aware ranking --------------------------------------
        public = (await api.get("/api/results")).json()
        names = [n["label"] for n in public["nodes"]]
        assert names == EXPECTED_TOP5
        assert [n["probe_score"] for n in public["nodes"]] == [4, 4, 4, 3, 3]
        assert all(n["probe_total"] == 4 for n in public["nodes"])
        raw = (await api.get("/api/vless/raw")).text.splitlines()
        assert [line.rsplit("#", 1)[1] for line in raw] == EXPECTED_TOP5

        # --- proof that the work was really done over the network ------------------------------
        # Exactly the probes whose networks let a candidate through completed a TLS handshake
        # with it, plus the rogue probe; nobody else (the liar never connected). The endpoint
        # threads may count the last handshakes a moment after the results arrived.
        expected = {port_of[name]: sum(1 for provider, _ in agents if provider in REACHES[name]) + 1
                    for name in "ABCDEFGH"}

        async def settled():
            return endpoints.handshakes == expected

        await wait_for(settled, timeout=10, what=f"handshakes {endpoints.handshakes} to equal {expected}")
        for provider, agent in agents:
            ports = {port for kind, _, port in agent.checker.executed if kind == "TLS_CONNECT"}
            assert ports == set(port_of.values()), f"{provider} probe did not attempt every candidate"
        assert liar.jobs_seen <= 3  # verification (+ control) only: never scan targets

        # --- a replayed result earns nothing -----------------------------------------------------
        _, some = agents[0]
        jobs = (await api.get(f"/api/admin/probes/{some.probe_id}/jobs", headers=admin)).json()["items"]
        done = next(j for j in jobs if j["purpose"] == "scan" and j["status"] == "done")
        replay = await api.post("/api/probes/results", headers={"Authorization": f"Bearer {some.probe_id}.{some.secret}"},
                                json={"results": [{"job_id": done["id"], "outcome": "SUCCESS"}]})
        assert replay.json() == {"accepted": 0, "rejected": [done["id"]]}
        assert [n["label"] for n in (await api.get("/api/results")).json()["nodes"]] == EXPECTED_TOP5

        # --- spot checks ran on honest probes and caught nobody ----------------------------------
        approved = (await api.get("/api/admin/probes", params={"status": "approved", "per_page": 200},
                                  headers=admin)).json()
        assert approved["total"] == 25
        # --- the contact phone is admin-only ------------------------------------------------------
        first = (await api.get(f"/api/admin/probes/{agents[0][1].probe_id}", headers=admin)).json()
        assert first["contact_phone"] == "+79120000000"
        for path in ("/api/results", "/api/providers", "/api/vless/top"):
            assert "9120000000" not in (await api.get(path)).text
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for _, agent in agents:
            await agent.close()
        await api.aclose()


async def _state(api, admin, probe_id, state):
    row = (await api.get(f"/api/admin/probes/{probe_id}", headers=admin)).json()
    return row["verification"]["state"] == state
