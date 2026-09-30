"""A faithful Python implementation of the scat-probe agent, for distributed tests.

Mirrors probe/internal/agent (job.go, checks.go, client.go, agent.go) contract for contract:
strict job decoding (unknown fields → the job is refused), the same validation (IP literal
host, allowed address, port blocklist, timeout bounds, per-type shape), *real* network checks
(TCP connect, TLS handshake with SNI and no certificate verification, one HTTP GET with a
verified certificate over TLS and no redirects), the same error classification, heartbeats,
long-polling for jobs, batched results with retries, and the same HTTP API.

Two things are injectable, exactly as the Go tests do:
* `allowed(ip)`: the address policy (production: public addresses only; tests: loopback);
* `network(ip, port)`: the simulated ISP between this probe and the internet. It returns
  None to let a connection through, "refused" (the ISP resets it: DPI) or "blackhole" (the
  ISP drops it: the check times out). This models *where* a probe is, not what it does:
  every allowed connection is a real socket connection.

`liar=True` makes a malicious client: it never runs any check and answers SUCCESS to
everything, to prove the backend does not give such a client credit.
"""
import asyncio
import errno
import ipaddress
import json
import re
import ssl
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx

JOB_FIELDS = {"job_id", "type", "host", "port", "timeout_ms", "server_name", "path", "tls"}
JOB_ID_RE = re.compile(r"^job_[0-9a-f]{20}$")
HOSTNAME_RE = re.compile(r"(?i)^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")
BLOCKED_PORTS = {0, 25, 135, 136, 137, 138, 139, 445, 465, 587}
MAX_HTTP_BODY = 64 * 1024


def public_addr(ip: str) -> bool:
    """job.go PublicAddr."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast)


@dataclass
class Job:
    job_id: str
    type: str
    host: str = ""
    port: int = 0
    timeout_ms: int = 0
    server_name: str = ""
    path: str = ""
    tls: bool = False


def decode_jobs(data: dict) -> list[Job]:
    """DecodeJobs: a job with unknown fields becomes an INVALID job (answered as ERROR)."""
    if set(data) != {"jobs"}:
        raise ValueError("unexpected envelope")
    jobs = []
    for raw in data["jobs"]:
        if not isinstance(raw, dict) or set(raw) - JOB_FIELDS:
            jobs.append(Job(job_id=str(raw.get("job_id", "")) if isinstance(raw, dict) else "", type="INVALID"))
            continue
        jobs.append(Job(**{k: raw[k] for k in raw}))
    return jobs


def validate(job: Job, allowed: Callable[[str], bool]) -> Optional[str]:
    if not JOB_ID_RE.match(job.job_id or ""):
        return "bad job id"
    if job.type not in ("TCP_CONNECT", "TLS_CONNECT", "HTTP_GET"):
        return "unsupported type"
    try:
        ipaddress.ip_address(job.host)
    except ValueError:
        return "host must be an IP literal"
    if not allowed(job.host):
        return "non-public address"
    if not 1 <= job.port <= 65535 or job.port in BLOCKED_PORTS:
        return "port not allowed"
    if not 500 <= job.timeout_ms <= 10_000:
        return "timeout out of range"
    if job.server_name:
        if len(job.server_name) > 253 or not HOSTNAME_RE.match(job.server_name):
            return "bad server name"
        try:
            ipaddress.ip_address(job.server_name)
            return "server name is an IP"
        except ValueError:
            pass
    if job.type == "TCP_CONNECT" and (job.server_name or job.path or job.tls):
        return "TCP_CONNECT takes only host and port"
    if job.type == "TLS_CONNECT" and (not job.server_name or job.path):
        return "TLS_CONNECT needs a server name and no path"
    if job.type == "HTTP_GET" and (not job.server_name or len(job.path) > 256 or not PATH_RE.match(job.path)
                                   or ".." in job.path or "//" in job.path):
        return "HTTP_GET needs a Host name and a plain path"
    return None


class Blocked(Exception):
    pass


def classify(exc: BaseException) -> tuple[str, str]:
    """checks.go classify."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "TIMEOUT", "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "FAILED", "connection_refused"
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError, EOFError)):
        return "FAILED", "connection_reset"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "FAILED", "tls_certificate"
    if isinstance(exc, OSError) and exc.errno == errno.EHOSTUNREACH:
        return "FAILED", "host_unreachable"
    if isinstance(exc, OSError) and exc.errno in (errno.ENETUNREACH, errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT):
        return "ERROR", "network_unreachable"
    return "FAILED", "failed"


@dataclass
class Checker:
    version: str = "1.0.0"
    allowed: Callable[[str], bool] = public_addr
    network: Callable[[str, int], Optional[str]] = lambda ip, port: None
    executed: list = field(default_factory=list)  # (job type, host, port) of every real connection attempt

    async def _dial(self, job: Job, ssl_ctx: Optional[ssl.SSLContext] = None, server_hostname: Optional[str] = None):
        self.executed.append((job.type, job.host, job.port))
        verdict = self.network(job.host, job.port)
        if verdict == "refused":
            raise ConnectionRefusedError(errno.ECONNREFUSED, "reset by the simulated ISP")
        if verdict == "blackhole":
            await asyncio.sleep(3600)  # dropped: only the job's timeout ends this
        return await asyncio.open_connection(job.host, job.port, ssl=ssl_ctx, server_hostname=server_hostname)

    async def run(self, job: Job) -> dict:
        if validate(job, self.allowed):
            return {"job_id": job.job_id, "outcome": "ERROR", "error": "invalid_job"}
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(self._check(job), timeout=job.timeout_ms / 1000)
        except BaseException as exc:  # noqa: BLE001 - every failure becomes an outcome
            if isinstance(exc, asyncio.CancelledError):
                raise
            outcome, error = classify(exc)
            if outcome == "FAILED" and error == "failed" and job.type == "TLS_CONNECT":
                error = "tls_handshake"
            if isinstance(exc, ssl.SSLError) and not isinstance(exc, ssl.SSLCertVerificationError) \
                    and job.type == "TLS_CONNECT":
                error = "tls_handshake"
            result = {"outcome": outcome, "error": error}
        result["job_id"] = job.job_id
        if result["outcome"] == "SUCCESS":
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return result

    async def _check(self, job: Job) -> dict:
        if job.type == "TCP_CONNECT":
            _, writer = await self._dial(job)
            writer.close()
            return {"outcome": "SUCCESS"}
        if job.type == "TLS_CONNECT":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE  # reachability only, like the Go agent
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.set_alpn_protocols(["h2", "http/1.1"])
            _, writer = await self._dial(job, ctx, job.server_name)
            version = writer.get_extra_info("ssl_object").version()
            writer.close()
            return {"outcome": "SUCCESS", "tls_version": {"TLSv1.3": "TLS 1.3", "TLSv1.2": "TLS 1.2"}.get(version, version)}
        # HTTP_GET: pinned to the job's IP, Host = server name, no redirects, body capped and dropped.
        ctx = ssl.create_default_context() if job.tls else None
        reader, writer = await self._dial(job, ctx, job.server_name if job.tls else None)
        host = job.server_name if job.port in ((443,) if job.tls else (80,)) else f"{job.server_name}:{job.port}"
        writer.write(f"GET {job.path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: scat-probe/{self.version}\r\n"
                     "Accept-Encoding: identity\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        status_line = await reader.readline()
        if not status_line:
            raise asyncio.IncompleteReadError(b"", None)
        status = int(status_line.split()[1])
        await reader.read(MAX_HTTP_BODY)
        writer.close()
        return {"outcome": "SUCCESS", "http_status": status}


class ProbeAgent:
    """agent.go Agent + client.go Client over the real HTTP API."""

    def __init__(self, api: str, checker: Checker, *, version: str = "1.0.0", poll_wait: int = 2,
                 concurrency: int = 4, liar: bool = False, delay: float = 0.0, public_ip: Optional[str] = None):
        self.api = api.rstrip("/")
        self.checker = checker
        self.version = version
        self.poll_wait = poll_wait
        self.concurrency = concurrency
        self.liar = liar
        self.delay = delay  # a slow device: every check takes this much longer
        self.probe_id = self.secret = ""
        self.heartbeat_every = 30.0
        self.status = ""
        self.jobs_seen = 0
        self.results_sent = 0
        # The reverse proxy in front of the API (Caddy) tells it each probe's address; tests
        # stand in for it so that every simulated device has its own public IP.
        headers = {"X-Forwarded-For": public_ip} if public_ip else {}
        self.http = httpx.AsyncClient(timeout=45, trust_env=False, headers=headers)

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.probe_id}.{self.secret}", "User-Agent": f"scat-probe/{self.version}"}

    async def register(self, token: str, profile: str = "linux-amd64", platform: str = "linux/amd64") -> str:
        reply = await self.http.post(f"{self.api}/api/probes/register", json={
            "token": token, "agent_version": self.version, "profile": profile, "platform": platform})
        reply.raise_for_status()
        data = reply.json()
        self.probe_id, self.secret = data["probe_id"], data["secret"]
        return self.probe_id

    async def heartbeat(self) -> dict:
        reply = await self.http.post(f"{self.api}/api/probes/heartbeat", headers=self._auth(),
                                     json={"agent_version": self.version, "uptime_seconds": 1, "jobs_running": 0})
        reply.raise_for_status()
        data = reply.json()
        self.status = data["status"]
        if 5 <= data.get("heartbeat_seconds", 0) <= 600:
            self.heartbeat_every = data["heartbeat_seconds"]
        return data

    async def _heartbeat_loop(self):
        while True:
            try:
                await self.heartbeat()
            except httpx.HTTPError:
                pass
            await asyncio.sleep(self.heartbeat_every)

    async def _run_one(self, job: Job, semaphore: asyncio.Semaphore) -> dict:
        async with semaphore:
            if self.liar:
                return {"job_id": job.job_id, "outcome": "SUCCESS", "latency_ms": 1.0}
            if self.delay:
                await asyncio.sleep(self.delay)
            return await self.checker.run(job)

    async def poll_once(self, wait: Optional[int] = None) -> int:
        reply = await self.http.get(f"{self.api}/api/probes/jobs", headers=self._auth(),
                                    params={"wait": self.poll_wait if wait is None else wait, "limit": self.concurrency * 4})
        reply.raise_for_status()
        jobs = decode_jobs(reply.json())
        if not jobs:
            return 0
        self.jobs_seen += len(jobs)
        semaphore = asyncio.Semaphore(self.concurrency)
        results = await asyncio.gather(*(self._run_one(j, semaphore) for j in jobs))
        results = [r for r in results if JOB_ID_RE.match(r["job_id"] or "")]
        for start in range(0, len(results), 50):
            for attempt in range(3):
                reply = await self.http.post(f"{self.api}/api/probes/results", headers=self._auth(),
                                             json={"results": results[start:start + 50]})
                if reply.status_code < 500:
                    break
                await asyncio.sleep(0.2 * (attempt + 1))
        self.results_sent += len(results)
        return len(results)

    async def _job_loop(self):
        while True:
            try:
                await self.poll_once()
            except httpx.HTTPError:
                await asyncio.sleep(0.5)

    async def run(self):
        await asyncio.gather(self._heartbeat_loop(), self._job_loop())

    async def close(self):
        await self.http.aclose()


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))
