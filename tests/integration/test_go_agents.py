"""25 probes running the real Go agent (probe/internal/agent), end to end.

The same scenario as test_distributed_probes.py, but the agents are the production Go code:
the test binary is compiled from probe/internal/agent/integration_test.go with a local `go`
or, without one, inside the golang:1.24-alpine image if it is already present (nothing is
downloaded). Skipped when neither is available.

    pytest -m integration tests/integration/test_go_agents.py -v
"""
import asyncio
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from tests.integration.harness import EXPECTED_SCORES, EXPECTED_TOP5, PROBES_PER_PROVIDER, PROVIDERS, REACHES

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
GO_IMAGE = "golang:1.24-alpine"


def _goarch() -> str:
    machine = platform.machine().lower()
    return {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)


def build_agent_test(out_dir: Path) -> Path:
    binary = out_dir / "agent-integration.test"
    args = ["test", "-c", "-tags", "integration", "-o"]
    if shutil.which("go"):
        subprocess.run(["go", *args, str(binary), "./internal/agent"], cwd=ROOT / "probe", check=True,
                       capture_output=True, env={**os.environ, "CGO_ENABLED": "0"})
        return binary
    docker = shutil.which("docker")
    if not docker or subprocess.run([docker, "image", "inspect", GO_IMAGE], capture_output=True).returncode != 0:
        pytest.skip(f"needs a Go toolchain or the {GO_IMAGE} image")
    subprocess.run([docker, "run", "--rm", "--network", "none", "-v", f"{ROOT / 'probe'}:/src:ro", "-v", f"{out_dir}:/out",
                    "-w", "/src", "-e", "CGO_ENABLED=0", "-e", f"GOOS={platform.system().lower()}",
                    "-e", f"GOARCH={_goarch()}", "-e", "GOFLAGS=-buildvcs=false", "-e", "GOCACHE=/tmp/gocache",
                    GO_IMAGE, "go", *args, "/out/agent-integration.test", "./internal/agent"],
                   check=True, capture_output=True)
    return binary


def test_25_real_go_agents(distributed, tmp_path):
    binary = build_agent_test(tmp_path)
    asyncio.run(scenario(distributed, binary, tmp_path))


async def scenario(env, binary: Path, tmp_path: Path):
    server, admin, port_of, endpoints = env["server"], env["admin"], env["port_of"], env["endpoints"]
    api = httpx.AsyncClient(base_url=server.url, timeout=30)
    process = None
    try:
        plan = []
        for provider in PROVIDERS:
            blocked = [port_of[name] for name, reaches in REACHES.items() if provider not in reaches]
            for i in range(PROBES_PER_PROVIDER[provider]):
                reply = await api.post("/api/providers", json={"name": provider, "country": "RU"})
                plan.append({"token": reply.json()["token"], "public_ip": f"198.51.{PROVIDERS.index(provider)}.{10 + i}",
                             "blocked_ports": blocked})
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan))
        process = subprocess.Popen(
            [str(binary), "-test.run", "TestIntegrationAgents", "-test.v", "-test.timeout", "5m"],
            env={**os.environ, "SCAT_IT_API": server.url, "SCAT_IT_PLAN": str(plan_file), "SCAT_IT_SECONDS": "240"},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        async def poll(predicate, what, timeout=120):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError(f"Go agents exited early:\n{process.stdout.read()}")
                value = await predicate()
                if value:
                    return value
                await asyncio.sleep(0.5)
            raise AssertionError(f"timed out waiting for {what}")

        async def verified():
            data = (await api.get("/api/admin/probes", params={"status": "approved", "per_page": 200},
                                  headers=admin)).json()
            return data if data["total"] == 25 else None

        approved = await poll(verified, "25 Go probes to pass verification")
        assert all(p["verification"]["state"] == "passed" for p in approved["items"])

        assert (await api.post("/api/admin/scanner/run", headers=admin)).json()["outcome"] == "started"

        async def answered():
            ranking = (await api.get("/api/admin/cache", headers=admin)).json()["ranking"]
            return ranking if len(ranking) == 8 and all(r["probe_total"] == 4 for r in ranking) else None

        ranking = await poll(answered, "every provider to answer every candidate")
        assert {r["label"]: r["probe_score"] for r in ranking} == EXPECTED_SCORES

        public = (await api.get("/api/results")).json()["nodes"]
        assert [n["label"] for n in public] == EXPECTED_TOP5
        assert [n["probe_score"] for n in public] == [4, 4, 4, 3, 3]

        expected = {port_of[name]: sum(PROBES_PER_PROVIDER[p] for p in REACHES[name]) for name in "ABCDEFGH"}

        async def settled():
            return endpoints.handshakes == expected

        await poll(settled, f"handshakes {endpoints.handshakes} to equal {expected}", timeout=10)
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
        await api.aclose()
