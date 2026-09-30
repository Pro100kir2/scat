"""Probe coordination: enrollment, credentials, heartbeats, the job queue and aggregation.

Flow:
  website form → enrollment (one-time token) → installer diagnostics (SUPPORTED/UNSUPPORTED)
  → agent registers with the token → probe ID + secret → heartbeats, job polling, results.

A new probe is "pending" and must pass automatic verification before its results count:
on first contact it gets a job to fetch a one-time URL on this API. The server sees that
request arrive, so a client that only claims success cannot pass (see start_verification).

After every successful full scan, the best PROBE_TARGET_COUNT configs become targets;
every online *approved* probe gets one job per unique target it has no fresh result for.
Results are kept per probe and aggregated per provider: for the public reachability view
and for the probe-confirmation score that re-ranks the top configs (confirmations()).
"""
import asyncio
import hashlib
import hmac
import logging
import re
import secrets
import time
import unicodedata
from typing import Callable, Iterable, Literal, Optional
from urllib.parse import urlsplit

from app import config, events
from app.probes import profiles
from app.probes.releases import Manifest
from app.probes.store import Store
from app.probes.targets import (Target, TargetRejected, control_target, resolve_public, target_for_node,
                                validate_hostname)

logger = logging.getLogger("skat.probes")

Outcome = Literal["SUCCESS", "FAILED", "TIMEOUT", "ERROR"]
OUTCOMES = ("SUCCESS", "FAILED", "TIMEOUT", "ERROR")
# What the site shows per config and provider. Never conflated:
State = Literal["SUCCESS", "FAILED", "TIMEOUT", "CHECKING", "NO_PROBE", "OFFLINE", "NOT_TESTED"]

# What the website shows while someone installs a probe, and what the admin filters by.
Lifecycle = Literal["registered", "verifying", "verified", "verification_failed", "disabled"]
Purpose = Literal["scan", "control", "verify", "spot", "test"]
# Job purposes whose results are about the probe itself, never about a VPN endpoint.
PROBE_CHECKS = ("control", "verify", "spot", "test")

PROBE_ID_RE = re.compile(r"^prb_[0-9a-f]{16}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
VERIFY_PATH = "/api/probes/verify/"
ENROLLMENT_ID_RE = re.compile(r"^enr_[0-9a-f]{16}$")
JOB_ID_RE = re.compile(r"^job_[0-9a-f]{20}$")
TOKEN_PREFIX = "skenr_"

# Leases cover the check's own timeout plus the round trip home.
LEASE_GRACE_SECONDS = 30
CONTROL_MAX_AGE_SECONDS = 30 * 60
ROTATION_GRACE_SECONDS = 60 * 60
MAX_LONG_POLL_SECONDS = 25
CLEANUP_KEEP_SECONDS = 24 * 60 * 60
DIAG_KEEP_BYTES = 16 * 1024
VERIFY_TARGET_MAX_AGE = 3600

# Why verification failed, as codes; the website and the admin panel phrase them.
VERIFY_REASONS = {
    "never_connected": "the probe registered but never contacted the server again",
    "jobs_not_fetched": "the probe is online but did not fetch its verification jobs",
    "no_result": "the probe fetched the verification job but never reported a result",
    "api_unreachable": "the probe could not open the verification URL on this server",
    "unexpected_response": "the verification URL answered with an unexpected status through this network",
    "nonce_not_received": "the probe reported success, but the server never received its request",
    "probe_error": "the probe could not run the verification check",
    "server_misconfigured": "the server has no public URL to verify probes against (PUBLIC_API_URL)",
    "spot_check_failed": "the probe reported success for a check it demonstrably did not run",
    "superseded": "a newer verification replaced this one",
}


class ProbeAuthError(Exception):
    """401: unknown probe or wrong secret."""


class ProbeForbidden(Exception):
    """403: the probe exists but is disabled."""


class EnrollmentError(Exception):
    """The enrollment token is unknown, used or expired."""


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _new_id(prefix: str, nbytes: int = 8) -> str:
    return f"{prefix}{secrets.token_hex(nbytes)}"


def name_key(name: str) -> str:
    """Case- and whitespace-insensitive identity, so "МТС" and " мтс" are one provider.
    Different scripts ("MTS" vs "МТС") stay distinct: an admin merges those if needed."""
    text = unicodedata.normalize("NFKC", name).casefold()
    return re.sub(r"\s+", " ", text).strip()


class ProbeService:
    def __init__(self, store: Store, manifest: Callable[[], Manifest], clock: Callable[[], float] = time.time):
        self.store = store
        self.manifest = manifest
        self.clock = clock
        self._wakeups: dict[str, asyncio.Event] = {}
        self._control: Optional[Target] = None
        self._control_resolved_at = 0.0
        self._verify_ip: Optional[tuple[str, str, float]] = None
        self._activate: set[str] = set()
        self._rejected_logged: dict[str, float] = {}
        self.stats = {"jobs_created": 0, "jobs_reused": 0, "results_accepted": 0, "results_rejected": 0}

    # ================================================================== enrollment
    def create_enrollment(self, *, name: str, country: str, region: str = "", asn: Optional[int] = None,
                          info: str = "", client_ip: str = "", contact_phone: Optional[str] = None) -> dict:
        """`contact_phone` must already be normalised (phone.normalize_phone); it is stored for
        admins only and never returned by any public view."""
        now = self.clock()
        key = name_key(name)
        with self.store.transaction() as tx:
            row = tx.execute("SELECT * FROM providers WHERE name_key=? AND country=?", (key, country)).fetchone()
            if row is None:
                provider_id = _new_id("prv_")
                tx.execute(
                    "INSERT INTO providers (id, name, name_key, country, region, asn, info, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (provider_id, name.strip(), key, country, region or None, asn, info or None, now),
                )
            else:
                provider_id = row["id"]
            enrollment_id = _new_id("enr_")
            token = TOKEN_PREFIX + secrets.token_urlsafe(32)
            expires = now + config.PROBE_ENROLLMENT_TTL_SECONDS
            tx.execute(
                "INSERT INTO enrollments (id, provider_id, token_hash, region, asn, info, client_ip, created_at, expires_at,"
                " contact_phone) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (enrollment_id, provider_id, _hash(token), region or None, asn, info or None, client_ip, now, expires,
                 contact_phone),
            )
        return {"enrollment_id": enrollment_id, "token": token, "expires_at": expires,
                "provider": self._provider_public(self.store.one("SELECT * FROM providers WHERE id=?", (provider_id,)))}

    def _enrollment_by_token(self, token: str, *, allow_used: bool = False) -> dict:
        if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 128:
            raise EnrollmentError("invalid enrollment token")
        row = self.store.one("SELECT * FROM enrollments WHERE token_hash=?", (_hash(token),))
        if row is None:
            raise EnrollmentError("invalid enrollment token")
        if row["expires_at"] < self.clock():
            raise EnrollmentError("enrollment token expired, create a new one on the website")
        if row["used_at"] and not allow_used:
            raise EnrollmentError("enrollment token already used")
        return row

    def enrollment_status(self, enrollment_id: str, token: str) -> dict:
        """What the website's installation page shows while the user runs the installer."""
        if not ENROLLMENT_ID_RE.match(enrollment_id or ""):
            raise EnrollmentError("invalid enrollment")
        row = self._enrollment_by_token(token, allow_used=True)
        if row["id"] != enrollment_id:
            raise EnrollmentError("invalid enrollment")
        diagnostics = self.store.loads(row["diag_json"]) if row["diag_json"] else None
        probe = self.store.one("SELECT * FROM probes WHERE id=?", (row["probe_id"],)) if row["probe_id"] else None
        if probe:
            state = self.lifecycle(probe)
        elif row["diag_verdict"] == "UNSUPPORTED":
            state = "unsupported"
        elif row["diag_verdict"] == "SUPPORTED":
            state = "installing"
        else:
            state = "waiting"
        return {
            "enrollment_id": row["id"],
            "state": state,
            "expires_at": row["expires_at"],
            "diagnostics": diagnostics,
            "probe": self._probe_public(probe) if probe else None,
            "verification": self._verification_public(probe) if probe else None,
        }

    def lifecycle(self, probe: dict) -> Lifecycle:
        """One word for where a probe stands; online/offline is reported separately."""
        if probe["status"] == "disabled":
            return "disabled"
        if probe["status"] == "approved":
            return "verified"
        if probe.get("verify_state") == "failed":
            return "verification_failed"
        if probe.get("verify_state") == "running" or probe.get("last_seen"):
            return "verifying"
        return "registered"

    def _verification_public(self, probe: dict) -> dict:
        """What the installer's page may know: state and a reason code, nothing about the network."""
        running = self.store.one("SELECT deadline FROM verifications WHERE probe_id=? AND state='running'"
                                 " ORDER BY id DESC LIMIT 1", (probe["id"],))
        return {
            "state": probe.get("verify_state") or "none",
            "reason": probe.get("verify_reason") if probe.get("verify_state") == "failed" else None,
            "finished_at": probe.get("verify_at"),
            "deadline": running["deadline"] if running else None,
            "attempts": probe.get("verify_attempts") or 0,
            "will_retry": self._will_retry(probe),
            "online": self.is_online(probe),
        }

    def _will_retry(self, probe: dict) -> bool:
        return (probe.get("verify_state") == "failed" and probe["status"] == "pending"
                and (probe.get("verify_attempts") or 0) < config.PROBE_VERIFY_MAX_ATTEMPTS)

    # ================================================================== diagnostics
    def diagnose(self, diag: dict, token: Optional[str] = None) -> dict:
        """Evaluate a device. With a token, the verdict is also shown on the website."""
        manifest = self.manifest()
        verdict = profiles.evaluate(diag, manifest.sizes_kb())
        result = verdict.as_dict()
        release = manifest.get(verdict.profile) if verdict.supported else None
        result["release"] = (
            {"version": manifest.version, "profile": release.profile, "sha256": release.sha256, "size": release.size}
            if release else None
        )
        if token:
            row = self._enrollment_by_token(token)
            payload = self.store.dumps(result)
            if len(payload) > DIAG_KEEP_BYTES:
                payload = self.store.dumps({k: result[k] for k in ("verdict", "profile", "reasons", "warnings")})
            self.store.run(
                "UPDATE enrollments SET diag_verdict=?, diag_profile=?, diag_json=?, diag_at=? WHERE id=?",
                (verdict.verdict, verdict.profile, payload, self.clock(), row["id"]),
            )
        return result

    # ================================================================== registration & auth
    def register(self, token: str, *, agent_version: str, profile: str, platform: str, public_ip: str) -> dict:
        now = self.clock()
        probe_id = _new_id("prb_")
        secret = secrets.token_urlsafe(32)
        status = "approved" if config.PROBE_AUTO_APPROVE else "pending"
        verify_state = "skipped" if config.PROBE_AUTO_APPROVE else "none"
        with self.store.transaction() as tx:
            row = tx.execute("SELECT * FROM enrollments WHERE token_hash=?", (_hash(token),)).fetchone() \
                if token.startswith(TOKEN_PREFIX) and len(token) <= 128 else None
            if row is None:
                raise EnrollmentError("invalid enrollment token")
            if row["expires_at"] < now:
                raise EnrollmentError("enrollment token expired, create a new one on the website")
            if row["used_at"]:
                raise EnrollmentError("enrollment token already used")
            tx.execute(
                "INSERT INTO probes (id, provider_id, enrollment_id, status, secret_hash, credential_at, region, asn,"
                " platform, profile, agent_version, public_ip, created_at, contact_phone, verify_state)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (probe_id, row["provider_id"], row["id"], status, _hash(secret), now, row["region"], row["asn"],
                 platform, profile, agent_version, public_ip, now, row["contact_phone"], verify_state),
            )
            tx.execute("UPDATE enrollments SET used_at=?, probe_id=? WHERE id=?", (now, probe_id, row["id"]))
            provider = tx.execute("SELECT name, country FROM providers WHERE id=?", (row["provider_id"],)).fetchone()
        events.record("probe", "probe_registered", f"New probe {probe_id} registered ({profile})", probe_id=probe_id,
                      data={"status": status, "profile": profile, "platform": platform, "version": agent_version,
                            "provider": provider["name"], "country": provider["country"]}, ts=now)
        return {"probe_id": probe_id, "secret": secret, "status": status,
                "heartbeat_seconds": config.PROBE_HEARTBEAT_SECONDS}

    def authenticate(self, authorization: str) -> dict:
        """`Authorization: Bearer <probe_id>.<secret>`. Constant-time comparison; the
        previous secret keeps working for a grace period after a rotation."""
        scheme, _, credential = (authorization or "").partition(" ")
        probe_id, _, secret = credential.strip().partition(".")
        if scheme.lower() != "bearer" or not PROBE_ID_RE.match(probe_id) or not secret or len(secret) > 128:
            raise ProbeAuthError
        probe = self.store.one("SELECT * FROM probes WHERE id=?", (probe_id,))
        if probe is None:
            hmac.compare_digest(_hash(secret), _hash("dummy"))  # same work for unknown IDs
            raise ProbeAuthError
        digest = _hash(secret)
        ok = hmac.compare_digest(digest, probe["secret_hash"])
        if not ok and probe["prev_secret_hash"] and (probe["prev_secret_expires"] or 0) > self.clock():
            ok = hmac.compare_digest(digest, probe["prev_secret_hash"])
        if not ok:
            raise ProbeAuthError
        if probe["status"] == "disabled":
            raise ProbeForbidden
        return probe

    def rotate(self, probe: dict) -> dict:
        now = self.clock()
        secret = secrets.token_urlsafe(32)
        self.store.run(
            "UPDATE probes SET prev_secret_hash=secret_hash, prev_secret_expires=?, secret_hash=?, credential_at=? WHERE id=?",
            (now + ROTATION_GRACE_SECONDS, _hash(secret), now, probe["id"]),
        )
        logger.info("probe %s rotated its credentials", probe["id"])
        return {"probe_id": probe["id"], "secret": secret}

    # ================================================================== liveness
    def is_online(self, probe: dict, now: Optional[float] = None) -> bool:
        now = self.clock() if now is None else now
        return bool(probe.get("last_seen")) and now - probe["last_seen"] <= config.PROBE_OFFLINE_AFTER_SECONDS

    async def seen(self, probe: dict) -> None:
        """Any authenticated request proves the probe is alive. A probe that was offline gets
        the current targets, whichever request (heartbeat or job poll) arrives first."""
        now = self.clock()
        was_offline = not self.is_online(probe, now)
        self.store.run("UPDATE probes SET last_seen=? WHERE id=?", (now, probe["id"]))
        probe["last_seen"] = now
        if was_offline:
            logger.info("probe %s is online", probe["id"])
            if probe["status"] == "approved":
                await self.ensure_jobs_for_probe(probe["id"])
        if probe["status"] == "pending":
            state = probe.get("verify_state") or "none"
            if state == "none":
                await self.start_verification(probe["id"], "first_contact")
            elif state == "failed" and self._will_retry(probe) and \
                    now - (probe.get("verify_at") or 0) >= config.PROBE_VERIFY_RETRY_SECONDS:
                await self.start_verification(probe["id"], "auto_retry")

    async def heartbeat(self, probe: dict, *, agent_version: str, public_ip: str) -> dict:
        now = self.clock()
        await self.seen(probe)
        self.store.run(
            "UPDATE probes SET last_heartbeat=?, agent_version=?, public_ip=? WHERE id=?",
            (now, agent_version, public_ip, probe["id"]),
        )
        return {
            "status": probe["status"],
            "server_time": now,
            "heartbeat_seconds": config.PROBE_HEARTBEAT_SECONDS,
            "rotate_credentials": now - probe["credential_at"] > config.PROBE_CREDENTIAL_MAX_AGE_SECONDS,
        }

    def touch(self, probe_id: str) -> None:
        self.store.run("UPDATE probes SET last_seen=? WHERE id=?", (self.clock(), probe_id))

    # ================================================================== scheduling
    async def _control_target(self) -> Optional[Target]:
        if self._control is None or self.clock() - self._control_resolved_at > 3600:
            try:
                self._control = await control_target(config.PROBE_CONTROL_URL)
                self._control_resolved_at = self.clock()
            except TargetRejected as exc:
                logger.warning("control URL unusable: %s", exc)
        return self._control

    async def schedule_scan(self, entry: dict) -> dict:
        """Called after a full scan was saved: the new best configs become the probe targets.

        Hostnames are resolved here, once, and every address is checked to be public.
        Configs whose endpoint is rejected stay in the list as "not tested".
        """
        nodes = (entry.get("ranked") or entry.get("top") or [])[: config.PROBE_TARGET_COUNT]
        semaphore = asyncio.Semaphore(8)

        async def resolve(node: dict) -> tuple[Optional[Target], Optional[str]]:
            async with semaphore:
                try:
                    return await target_for_node(node), None
                except (TargetRejected, ValueError, TypeError) as exc:
                    return None, str(exc)[:200]

        resolved = await asyncio.gather(*(resolve(n) for n in nodes))
        with self.store.transaction() as tx:
            tx.execute("DELETE FROM targets")
            for rank, (node, (target, reason)) in enumerate(zip(nodes, resolved), 1):
                tx.execute(
                    "INSERT INTO targets (rank, fingerprint, key, payload, reject_reason, scan_created_at) VALUES (?,?,?,?,?,?)",
                    (rank, node["fingerprint"], target.key if target else None,
                     self.store.dumps(target.job_payload()) if target else None, reason, entry.get("created_at")),
                )
        targets = [t for t, _ in resolved if t is not None]
        rejected = len(nodes) - len(targets)
        if rejected:
            logger.warning("%d of %d probe targets rejected (non-public or unresolvable)", rejected, len(nodes))
        created = probes_used = 0
        # Only verified probes work on real targets; pending ones get verification jobs only.
        for probe in self.store.all("SELECT * FROM probes WHERE status = 'approved'"):
            if self.is_online(probe):
                probes_used += 1
                created += await self._enqueue(probe["id"], targets)
        logger.info("probe round: %d targets (%d unique), %d jobs created", len(targets), len({t.key for t in targets}), created)
        return {"targets": len(targets), "rejected": rejected, "jobs_created": created, "probes": probes_used}

    def current_targets(self) -> list[Target]:
        out = []
        for row in self.store.all("SELECT payload FROM targets WHERE payload IS NOT NULL ORDER BY rank"):
            try:
                out.append(Target.from_payload(self.store.loads(row["payload"])))
            except (TargetRejected, KeyError, ValueError):
                continue
        return out

    async def ensure_jobs_for_probe(self, probe_id: str) -> int:
        """An approved probe that (re)appears gets the current targets it has no fresh result for."""
        probe = self.store.one("SELECT status FROM probes WHERE id=?", (probe_id,))
        if not probe or probe["status"] != "approved":
            return 0
        return await self._enqueue(probe_id, self.current_targets())

    async def _enqueue(self, probe_id: str, targets: Iterable[Target]) -> int:
        now = self.clock()
        unique = {t.key: t for t in targets}
        created = reused = 0
        probe = self.store.one("SELECT control_at, spot_at, verify_at FROM probes WHERE id=?", (probe_id,))
        control = await self._control_target() if unique else None
        spot = None
        last_proof = max(probe["spot_at"] or 0, probe["verify_at"] or 0)
        if unique and config.PROBE_SPOT_CHECK_SECONDS > 0 and now - last_proof >= config.PROBE_SPOT_CHECK_SECONDS:
            spot = await self._nonce_target()
        with self.store.transaction() as tx:
            if control and (not probe["control_at"] or now - probe["control_at"] > CONTROL_MAX_AGE_SECONDS):
                created += self._insert_job(tx, probe_id, control, now, purpose="control")
            if spot:
                target, nonce = spot
                # Mixed in with the real work; created a moment later so it is not always first.
                created += self._insert_job(tx, probe_id, target, now + 0.5, purpose="spot", nonce=nonce)
                tx.execute("UPDATE probes SET spot_at=? WHERE id=?", (now, probe_id))
            for key, target in unique.items():
                fresh = tx.execute(
                    "SELECT 1 FROM results WHERE probe_id=? AND key=? AND expires_at>? AND outcome!='ERROR' LIMIT 1",
                    (probe_id, key, now),
                ).fetchone()
                if fresh:
                    reused += 1  # the cache answers; no network traffic needed
                    continue
                created += self._insert_job(tx, probe_id, target, now)
        self.stats["jobs_created"] += created
        self.stats["jobs_reused"] += reused
        if created:
            self._wake(probe_id)
        return created

    def _insert_job(self, tx, probe_id: str, target: Target, now: float, purpose: Purpose = "scan",
                    nonce: Optional[str] = None, verification_id: Optional[int] = None) -> int:
        is_control = purpose == "control"
        cursor = tx.execute(
            "INSERT OR IGNORE INTO jobs (id, probe_id, key, type, payload, is_control, status, created_at, expires_at,"
            " purpose, nonce, verification_id) VALUES (?,?,?,?,?,?, 'queued', ?, ?, ?, ?, ?)",
            (_new_id("job_", 10), probe_id, target.key, target.type, self.store.dumps(target.job_payload()),
             int(is_control), now - (1 if is_control else 0), now + config.PROBE_JOB_TTL_SECONDS,
             purpose, nonce, verification_id),
        )
        return cursor.rowcount

    def _wake(self, probe_id: str) -> None:
        event = self._wakeups.get(probe_id)
        if event:
            event.set()

    # ================================================================== job delivery
    def _lease(self, probe_id: str, limit: int) -> list[dict]:
        now = self.clock()
        jobs = []
        with self.store.transaction() as tx:
            rows = tx.execute(
                "SELECT * FROM jobs WHERE probe_id=? AND status='queued' AND expires_at>?"
                " ORDER BY is_control DESC, purpose='verify' DESC, created_at LIMIT ?",
                (probe_id, now, limit),
            ).fetchall()
            for row in rows:
                payload = self.store.loads(row["payload"])
                lease = now + payload["timeout_ms"] / 1000 + LEASE_GRACE_SECONDS
                tx.execute(
                    "UPDATE jobs SET status='leased', leased_at=?, lease_expires=?, attempts=attempts+1 WHERE id=?",
                    (now, lease, row["id"]),
                )
                jobs.append({"job_id": row["id"], **payload})
        return jobs

    async def lease_jobs(self, probe: dict, wait_seconds: float = 0, limit: Optional[int] = None) -> list[dict]:
        """Hand out queued jobs. Long-polls up to `wait_seconds` when there are none."""
        limit = min(limit or config.PROBE_JOBS_PER_POLL, config.PROBE_JOBS_PER_POLL)
        await self.seen(probe)
        jobs = self._lease(probe["id"], limit)
        wait_seconds = max(0.0, min(wait_seconds, MAX_LONG_POLL_SECONDS))
        if jobs or not wait_seconds:
            return jobs
        event = self._wakeups.setdefault(probe["id"], asyncio.Event())
        event.clear()
        try:
            await asyncio.wait_for(event.wait(), timeout=wait_seconds)
        except asyncio.TimeoutError:
            return []
        self.touch(probe["id"])
        return self._lease(probe["id"], limit)

    # ================================================================== results
    def submit_results(self, probe: dict, results: list[dict]) -> dict:
        """Accept results only for this probe's own open jobs; target data comes from the
        job, never from the probe, so a probe cannot inject endpoints or keys. A job takes
        exactly one answer: a replayed or duplicated result is rejected."""
        now = self.clock()
        accepted, rejected = 0, []
        checks: set[str] = set()
        control_failed_recently = (
            probe.get("control_ok") == 0 and probe.get("control_at") and now - probe["control_at"] < CONTROL_MAX_AGE_SECONDS
        )
        counters = {"done": 0, "success": 0, "failed": 0, "error": 0}
        last_error: Optional[str] = None
        with self.store.transaction() as tx:
            for item in results:
                job = tx.execute(
                    "SELECT * FROM jobs WHERE id=? AND probe_id=? AND status IN ('queued','leased')",
                    (item["job_id"], probe["id"]),
                ).fetchone()
                if job is None:
                    rejected.append(item["job_id"])
                    continue
                outcome = item["outcome"]
                tx.execute(
                    "UPDATE jobs SET status='done', completed_at=?, outcome=?, error=?, http_status=?, latency_ms=?"
                    " WHERE id=?",
                    (now, outcome, item.get("error"), item.get("http_status"),
                     item.get("latency_ms") if outcome == "SUCCESS" else None, job["id"]),
                )
                counters["done"] += 1
                if outcome == "SUCCESS":
                    counters["success"] += 1
                elif outcome == "ERROR":
                    counters["error"] += 1
                    last_error = item.get("error") or "error"
                else:
                    counters["failed"] += 1
                accepted += 1
                purpose = job["purpose"]
                if purpose == "control":
                    ok = outcome == "SUCCESS"
                    tx.execute("UPDATE probes SET control_ok=?, control_at=? WHERE id=?", (int(ok), now, probe["id"]))
                    control_failed_recently = not ok
                    continue
                if purpose in PROBE_CHECKS:
                    checks.add(purpose)
                    continue
                payload = self.store.loads(job["payload"])
                # A probe whose own internet is broken says nothing about the endpoint.
                inconclusive = outcome == "ERROR" or (outcome != "SUCCESS" and control_failed_recently)
                tx.execute(
                    "INSERT INTO results (job_id, probe_id, provider_id, key, type, ip, port, server_name, outcome, error,"
                    " latency_ms, inconclusive, checked_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (job["id"], probe["id"], probe["provider_id"], job["key"], job["type"], payload["host"],
                     payload["port"], payload.get("server_name"), outcome, item.get("error"),
                     item.get("latency_ms") if outcome == "SUCCESS" else None, int(bool(inconclusive)),
                     now, now + config.PROBE_RESULT_TTL_SECONDS),
                )
            if counters["done"]:
                tx.execute(
                    "UPDATE probes SET jobs_done=jobs_done+?, jobs_success=jobs_success+?, jobs_failed=jobs_failed+?,"
                    " jobs_error=jobs_error+?, last_job_at=?,"
                    " last_success_at=CASE WHEN ? > 0 THEN ? ELSE last_success_at END,"
                    " last_error=COALESCE(?, last_error), last_error_at=CASE WHEN ? IS NOT NULL THEN ? ELSE last_error_at END"
                    " WHERE id=?",
                    (counters["done"], counters["success"], counters["failed"], counters["error"], now,
                     counters["success"], now, last_error, last_error, now, probe["id"]),
                )
        self.stats["results_accepted"] += accepted
        self.stats["results_rejected"] += len(rejected)
        # One event per probe per 10 minutes: a misbehaving probe must not flood the log.
        if rejected and now - self._rejected_logged.get(probe["id"], 0) > 600:
            self._rejected_logged[probe["id"]] = now
            events.record("probe", "results_rejected", f"Probe {probe['id']} sent {len(rejected)} result(s) for jobs it "
                          "does not hold (duplicate, expired or foreign)", severity="warning", probe_id=probe["id"],
                          data={"count": len(rejected)}, ts=now)
        self.touch(probe["id"])
        if "spot" in checks:
            self._evaluate_spot_checks(probe["id"])
        if "verify" in checks or "control" in checks:
            self._evaluate_verification(probe["id"])
        return {"accepted": accepted, "rejected": rejected}

    # ================================================================== verification
    async def _nonce_target(self) -> Optional[tuple[Target, str]]:
        """An HTTP_GET job to a one-time URL on this API: /api/probes/verify/<nonce>.

        The request itself is the proof. The agent never returns response bodies, so the
        backend has to watch the request arrive; a client that only answers "SUCCESS"
        without making it fails. Returns None when the server has no public URL."""
        base = config.PROBE_VERIFY_URL or config.PUBLIC_API_URL
        parts = urlsplit(base) if base else None
        if not parts or parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        tls = parts.scheme == "https"
        port = parts.port or (443 if tls else 80)
        cached = self._verify_ip
        try:
            host = validate_hostname(parts.hostname)
            if cached and cached[0] == host and self.clock() - cached[2] < VERIFY_TARGET_MAX_AGE:
                ip = cached[1]
            else:
                ip = await resolve_public(host)
                self._verify_ip = (host, ip, self.clock())
            nonce = secrets.token_hex(16)
            return Target("HTTP_GET", ip, port, server_name=host, path=VERIFY_PATH + nonce, tls=tls), nonce
        except TargetRejected as exc:
            logger.warning("verification URL unusable: %s", exc)
            return None

    async def start_verification(self, probe_id: str, trigger: str) -> Optional[int]:
        """Queue a verification round for one probe. At most one runs at a time per probe;
        a new one supersedes the previous (its jobs are withdrawn)."""
        now = self.clock()
        probe = self.store.one("SELECT * FROM probes WHERE id=?", (probe_id,))
        if probe is None or probe["status"] == "disabled":
            return None
        spot = await self._nonce_target()
        control = await self._control_target()
        with self.store.transaction() as tx:
            # Claim atomically: two requests of the same probe must not both start one.
            if trigger in ("first_contact", "auto_retry"):
                claimed = tx.execute(
                    "UPDATE probes SET verify_state='running' WHERE id=? AND status='pending'"
                    " AND verify_state IN ('none','failed')", (probe_id,)).rowcount
                if not claimed:
                    return None
            for old in tx.execute("SELECT id FROM verifications WHERE probe_id=? AND state='running'", (probe_id,)).fetchall():
                tx.execute("UPDATE verifications SET state='superseded', reason='superseded', finished_at=? WHERE id=?",
                           (now, old["id"]))
                tx.execute("UPDATE jobs SET status='expired', completed_at=? WHERE verification_id=?"
                           " AND status IN ('queued','leased')", (now, old["id"]))
            if spot is None:
                cursor = tx.execute(
                    "INSERT INTO verifications (probe_id, trigger, state, reason, started_at, deadline, finished_at)"
                    " VALUES (?,?, 'failed', 'server_misconfigured', ?, ?, ?)", (probe_id, trigger, now, now, now))
                tx.execute("UPDATE probes SET verify_state='failed', verify_reason='server_misconfigured', verify_at=?,"
                           " verify_attempts=verify_attempts+1 WHERE id=?", (now, probe_id))
                verification_id = cursor.lastrowid
            else:
                deadline = now + config.PROBE_VERIFY_TIMEOUT_SECONDS
                cursor = tx.execute(
                    "INSERT INTO verifications (probe_id, trigger, state, started_at, deadline) VALUES (?,?, 'running', ?, ?)",
                    (probe_id, trigger, now, deadline))
                verification_id = cursor.lastrowid
                target, nonce = spot
                self._insert_job(tx, probe_id, target, now, purpose="verify", nonce=nonce, verification_id=verification_id)
                if control:
                    # Not required to pass (some networks block the control host); recorded for the admin.
                    tx.execute("UPDATE jobs SET status='expired', completed_at=? WHERE probe_id=? AND purpose='control'"
                               " AND status IN ('queued','leased')", (now, probe_id))
                    self._insert_job(tx, probe_id, control, now, purpose="control", verification_id=verification_id)
                tx.execute("UPDATE probes SET verify_state='running', verify_reason=NULL,"
                           " verify_attempts=verify_attempts+1 WHERE id=?", (probe_id,))
        if spot is None:
            events.record("verification", "verification_failed", f"Probe {probe_id} cannot be verified: set PUBLIC_API_URL",
                          severity="error", probe_id=probe_id, data={"reason": "server_misconfigured"}, ts=now)
            return verification_id
        events.record("verification", "verification_started", f"Verification of probe {probe_id} started ({trigger})",
                      probe_id=probe_id, data={"trigger": trigger, "verification_id": verification_id}, ts=now)
        self._wake(probe_id)
        return verification_id

    def nonce_hit(self, nonce: str, client_ip: str) -> bool:
        """The verification URL was requested. Only the first request of an open job counts."""
        if not NONCE_RE.match(nonce or ""):
            return False
        return self.store.run(
            "UPDATE jobs SET nonce_hit_at=?, nonce_hit_ip=? WHERE nonce=? AND nonce_hit_at IS NULL"
            " AND status IN ('queued','leased')", (self.clock(), client_ip[:64], nonce)) > 0

    @staticmethod
    def _nonce_verdict(job: dict) -> Optional[str]:
        """None when the one-time URL check passed, else a reason code."""
        if job["outcome"] == "SUCCESS":
            if not job["nonce_hit_at"]:
                return "nonce_not_received"
            if job["http_status"] != 204:
                return "unexpected_response"
            return None
        if job["outcome"] == "ERROR":
            return "probe_error"
        return "api_unreachable"

    def _evaluate_verification(self, probe_id: str) -> None:
        row = self.store.one("SELECT * FROM verifications WHERE probe_id=? AND state='running' ORDER BY id DESC LIMIT 1",
                             (probe_id,))
        if row is None:
            return
        jobs = self.store.all("SELECT * FROM jobs WHERE verification_id=?", (row["id"],))
        nonce_job = next((j for j in jobs if j["purpose"] == "verify"), None)
        if nonce_job is None or nonce_job["status"] != "done":
            return  # still waiting for the decisive answer
        reason = self._nonce_verdict(nonce_job)
        self._finish_verification(row, jobs, reason)

    def _finish_verification(self, row: dict, jobs: list[dict], reason: Optional[str]) -> None:
        now = self.clock()
        probe_id = row["probe_id"]
        detail = {
            "checks": [
                {"purpose": j["purpose"], "type": j["type"], "status": j["status"], "outcome": j["outcome"],
                 "error": j["error"], "http_status": j["http_status"], "latency_ms": j["latency_ms"],
                 "request_seen": bool(j["nonce_hit_at"]) if j["purpose"] == "verify" else None,
                 "request_ip_matches": (j["nonce_hit_ip"] == self._probe_ip(probe_id)) if j["nonce_hit_at"] else None}
                for j in jobs
            ],
        }
        passed = reason is None
        with self.store.transaction() as tx:
            finished = tx.execute(
                "UPDATE verifications SET state=?, reason=?, detail=?, finished_at=? WHERE id=? AND state='running'",
                ("passed" if passed else "failed", reason, self.store.dumps(detail), now, row["id"])).rowcount
            if not finished:
                return
            if passed:
                tx.execute("UPDATE probes SET verify_state='passed', verify_reason=NULL, verify_at=?, spot_at=?,"
                           " status=CASE WHEN status='pending' THEN 'approved' ELSE status END WHERE id=?",
                           (now, now, probe_id))
            else:
                # A failed re-run takes an approved probe out of scoring until it passes again.
                tx.execute("UPDATE probes SET verify_state='failed', verify_reason=?, verify_at=?,"
                           " status=CASE WHEN status='approved' THEN 'pending' ELSE status END WHERE id=?",
                           (reason, now, probe_id))
            tx.execute("UPDATE jobs SET status='expired', completed_at=? WHERE verification_id=?"
                       " AND status IN ('queued','leased')", (now, row["id"]))
        if passed:
            self._activate.add(probe_id)
            events.record("verification", "probe_verified", f"Probe {probe_id} passed verification and now takes part "
                          "in scanning", probe_id=probe_id, data={"verification_id": row["id"]}, ts=now)
        else:
            events.record("verification", "verification_failed",
                          f"Probe {probe_id} failed verification: {VERIFY_REASONS.get(reason, reason)}",
                          severity="warning", probe_id=probe_id, data={"reason": reason, "verification_id": row["id"]},
                          ts=now)

    def _probe_ip(self, probe_id: str) -> Optional[str]:
        row = self.store.one("SELECT public_ip FROM probes WHERE id=?", (probe_id,))
        return row["public_ip"] if row else None

    def _expire_verifications(self) -> int:
        """Rounds past their deadline fail with the most specific reason the jobs allow."""
        now = self.clock()
        failed = 0
        for row in self.store.all("SELECT * FROM verifications WHERE state='running' AND deadline<?", (now,)):
            jobs = self.store.all("SELECT * FROM jobs WHERE verification_id=?", (row["id"],))
            nonce_job = next((j for j in jobs if j["purpose"] == "verify"), None)
            probe = self.store.one("SELECT last_seen FROM probes WHERE id=?", (row["probe_id"],))
            if nonce_job and nonce_job["status"] == "done":
                reason = self._nonce_verdict(nonce_job)
            elif not probe or not probe["last_seen"] or probe["last_seen"] < row["started_at"]:
                reason = "never_connected"
            elif nonce_job and nonce_job["attempts"]:
                reason = "no_result"
            else:
                reason = "jobs_not_fetched"
            self._finish_verification(row, jobs, reason)
            failed += 1
        return failed

    def _evaluate_spot_checks(self, probe_id: str) -> None:
        """A spot check answered SUCCESS although its URL was never requested: the probe
        invents results. It loses its approval and every result it has in the pool."""
        lied = self.store.one(
            "SELECT id FROM jobs WHERE probe_id=? AND purpose='spot' AND status='done' AND outcome='SUCCESS'"
            " AND nonce_hit_at IS NULL LIMIT 1", (probe_id,))
        if not lied:
            return
        now = self.clock()
        with self.store.transaction() as tx:
            tx.execute("UPDATE probes SET status=CASE WHEN status='approved' THEN 'pending' ELSE status END,"
                       " verify_state='failed', verify_reason='spot_check_failed', verify_at=?,"
                       " verify_attempts=MAX(verify_attempts, ?) WHERE id=?",
                       (now, config.PROBE_VERIFY_MAX_ATTEMPTS, probe_id))
            tx.execute("UPDATE results SET inconclusive=1 WHERE probe_id=?", (probe_id,))
            tx.execute("UPDATE jobs SET status='expired', completed_at=? WHERE probe_id=? AND status IN ('queued','leased')",
                       (now, probe_id))
            tx.execute("UPDATE jobs SET purpose='spot_failed' WHERE id=?", (lied["id"],))
            tx.execute("INSERT INTO verifications (probe_id, trigger, state, reason, started_at, deadline, finished_at)"
                       " VALUES (?, 'spot_check', 'failed', 'spot_check_failed', ?, ?, ?)", (probe_id, now, now, now))
        events.record("verification", "spot_check_failed", f"Probe {probe_id} reported success for a check it did not "
                      "run; its approval and results were withdrawn", severity="error", probe_id=probe_id, ts=now)

    async def flush_activations(self) -> int:
        """Probes that just passed verification get the current targets."""
        created = 0
        while self._activate:
            created += await self.ensure_jobs_for_probe(self._activate.pop())
        return created

    # ================================================================== maintenance
    def maintain(self) -> dict:
        """Requeue lost leases, expire stale jobs, drop old data. Idempotent; runs periodically."""
        now = self.clock()
        with self.store.transaction() as tx:
            retried = tx.execute(
                "UPDATE jobs SET status='queued', leased_at=NULL, lease_expires=NULL"
                " WHERE status='leased' AND lease_expires<? AND attempts<? AND expires_at>?",
                (now, config.PROBE_JOB_MAX_ATTEMPTS, now),
            ).rowcount
            expiring = "(status='leased' AND lease_expires<?) OR (status='queued' AND expires_at<=?)"
            for row in tx.execute(f"SELECT probe_id, COUNT(*) AS n FROM jobs WHERE {expiring} GROUP BY probe_id",
                                  (now, now)).fetchall():
                tx.execute("UPDATE probes SET jobs_expired=jobs_expired+? WHERE id=?", (row["n"], row["probe_id"]))
            expired = tx.execute(f"UPDATE jobs SET status='expired', completed_at=? WHERE {expiring}",
                                 (now, now, now)).rowcount
            tx.execute("DELETE FROM jobs WHERE status IN ('done','expired') AND completed_at<?", (now - CLEANUP_KEEP_SECONDS,))
            tx.execute("DELETE FROM results WHERE expires_at<?", (now - CLEANUP_KEEP_SECONDS,))
            tx.execute("DELETE FROM enrollments WHERE probe_id IS NULL AND expires_at<?", (now - CLEANUP_KEEP_SECONDS,))
        self._expire_verifications()
        if retried:
            for probe_id in list(self._wakeups):
                self._wake(probe_id)
        return {"retried": retried, "expired": expired}

    async def run_forever(self, interval: float = 15.0) -> None:
        while True:
            try:
                self.maintain()
                await self.flush_activations()
                events.prune(self.clock())
            except Exception:
                logger.exception("probe maintenance failed")
            await asyncio.sleep(interval)

    # ================================================================== public views
    def _provider_public(self, row: dict) -> dict:
        return {"id": row["id"], "name": row["name"], "country": row["country"], "region": row["region"], "asn": row["asn"]}

    def _probe_public(self, probe: dict, provider: Optional[dict] = None) -> dict:
        """Never includes the volunteer's IP address or any credential."""
        profile = probe.get("profile") or ""
        return {
            "id": probe["id"],
            "provider": provider["name"] if provider else None,
            "status": probe["status"],
            "online": self.is_online(probe),
            "last_seen": probe.get("last_seen"),
            "region": probe.get("region") or (provider or {}).get("region"),
            "asn": probe.get("asn") or (provider or {}).get("asn"),
            "platform": profiles.PROFILES.get(profile, profile or None),
            "version": probe.get("agent_version"),
        }

    def _listed_providers(self) -> list[dict]:
        """Providers that ever had an approved probe; pending-only providers stay private."""
        return self.store.all(
            "SELECT DISTINCT p.* FROM providers p JOIN probes pr ON pr.provider_id = p.id"
            " WHERE pr.status IN ('approved','disabled') ORDER BY p.name COLLATE NOCASE"
        )

    def providers_overview(self) -> dict:
        now = self.clock()
        probes = self.store.all("SELECT * FROM probes WHERE status='approved'")
        providers = []
        for row in self._listed_providers():
            own = [p for p in probes if p["provider_id"] == row["id"]]
            online = [p for p in own if self.is_online(p, now)]
            regions = sorted({p["region"] for p in own if p["region"]} | ({row["region"]} if row["region"] else set()))
            asns = sorted({p["asn"] for p in own if p["asn"]} | ({row["asn"]} if row["asn"] else set()))
            providers.append({
                **self._provider_public(row),
                "regions": regions,
                "asns": asns,
                "probes_total": len(own),
                "probes_online": len(online),
                "status": "online" if online else ("offline" if own else "no_probe"),
                "last_heartbeat": max((p["last_seen"] or 0 for p in own), default=None) or None,
            })
        by_provider = {r["id"]: r for r in self._listed_providers()}
        dashboard = [
            self._probe_public(p, by_provider.get(p["provider_id"]))
            for p in sorted(probes, key=lambda p: (-(p["last_seen"] or 0), p["id"]))
        ]
        return {"providers": providers, "probes": dashboard, "offline_after_seconds": config.PROBE_OFFLINE_AFTER_SECONDS}

    def reachability(self, fingerprints: list[str]) -> dict[str, list[dict]]:
        """Per config and provider: SUCCESS / FAILED / TIMEOUT / CHECKING / NO_PROBE / OFFLINE / NOT_TESTED.

        A provider counts as reaching an endpoint when at least one of its approved probes
        did. Individual probe results stay in the database for finer views later.
        """
        providers = self._listed_providers()
        if not fingerprints or not providers:
            return {fp: [] for fp in fingerprints}
        now = self.clock()
        targets = {
            r["fingerprint"]: r
            for r in self.store.all(
                f"SELECT fingerprint, key, reject_reason FROM targets WHERE fingerprint IN ({','.join('?' * len(fingerprints))})",
                fingerprints,
            )
        }
        keys = sorted({t["key"] for t in targets.values() if t["key"]})
        probes = self.store.all("SELECT * FROM probes WHERE status='approved'")
        approved = {p["id"]: p for p in probes}
        results: dict[tuple[str, str], list[dict]] = {}
        open_jobs: set[tuple[str, str]] = set()
        if keys:
            marks = ",".join("?" * len(keys))
            for r in self.store.all(
                f"SELECT * FROM results WHERE key IN ({marks}) AND expires_at>? AND inconclusive=0 ORDER BY checked_at DESC",
                [*keys, now],
            ):
                if r["probe_id"] in approved:
                    results.setdefault((r["key"], r["provider_id"]), []).append(r)
            for j in self.store.all(
                f"SELECT key, probe_id FROM jobs WHERE key IN ({marks}) AND status IN ('queued','leased')", keys
            ):
                probe = approved.get(j["probe_id"])
                if probe and self.is_online(probe, now):
                    open_jobs.add((j["key"], probe["provider_id"]))

        out: dict[str, list[dict]] = {}
        for fp in fingerprints:
            target = targets.get(fp)
            rows = []
            for prov in providers:
                own = [p for p in probes if p["provider_id"] == prov["id"]]
                state, latency, checked = self._state(target, prov["id"], own, results, open_jobs, now)
                rows.append({"provider_id": prov["id"], "provider": prov["name"], "state": state,
                             "latency_ms": latency, "checked_at": checked,
                             "probes": len(own)})
            out[fp] = rows
        return out

    def _state(self, target, provider_id, own_probes, results, open_jobs, now) -> tuple[State, Optional[float], Optional[float]]:
        if not own_probes:
            return "NO_PROBE", None, None
        if not target or not target["key"]:
            return "NOT_TESTED", None, None
        key = target["key"]
        rows = results.get((key, provider_id), [])
        ok = [r for r in rows if r["outcome"] == "SUCCESS"]
        if ok:
            best = min((r["latency_ms"] for r in ok if r["latency_ms"] is not None), default=None)
            return "SUCCESS", (round(best) if best is not None else None), max(r["checked_at"] for r in ok)
        if (key, provider_id) in open_jobs:
            return "CHECKING", None, None
        if rows:
            state: State = "FAILED" if any(r["outcome"] == "FAILED" for r in rows) else "TIMEOUT"
            return state, None, rows[0]["checked_at"]
        if not any(self.is_online(p, now) for p in own_probes):
            return "OFFLINE", None, None
        return "NOT_TESTED", None, None

    # ================================================================== scoring
    def confirmations(self, fingerprints: list[str]) -> dict[str, dict]:
        """Probe confirmations per config: {fingerprint: {score, total, providers}}.

        score = number of *providers* whose approved probes confirmed the config's endpoint
        reachable (outcome SUCCESS, conclusive, not expired). Per probe only its latest
        conclusive result for the target counts, so repeated or duplicated answers never add
        up, and a provider gives at most one point however many probes it has. One public
        IP backs at most one provider's point, so registering several "providers" from one
        connection gains nothing. total = providers with a conclusive latest result.

        Pending, disabled and spot-check-failed probes never count; results of a probe that
        has since gone offline still count until they expire (they were real observations).
        """
        out = {fp: {"score": 0, "total": 0, "providers": []} for fp in fingerprints}
        if not fingerprints:
            return out
        now = self.clock()
        marks = ",".join("?" * len(fingerprints))
        targets = self.store.all(f"SELECT fingerprint, key FROM targets WHERE key IS NOT NULL AND fingerprint IN ({marks})",
                                 fingerprints)
        keys = sorted({t["key"] for t in targets})
        if not keys:
            return out
        probes = {p["id"]: p for p in self.store.all(
            "SELECT p.id, p.provider_id, p.public_ip, pr.name AS provider FROM probes p"
            " JOIN providers pr ON pr.id = p.provider_id WHERE p.status='approved'")}
        latest: dict[tuple[str, str], dict] = {}
        key_marks = ",".join("?" * len(keys))
        for r in self.store.all(
            f"SELECT probe_id, key, outcome, checked_at FROM results WHERE key IN ({key_marks}) AND expires_at>?"
            " AND inconclusive=0 ORDER BY checked_at DESC, id DESC", [*keys, now],
        ):
            if r["probe_id"] in probes:
                latest.setdefault((r["probe_id"], r["key"]), r)  # rows are newest first
        per_key: dict[str, dict] = {}
        for key in keys:
            answered: set[str] = set()
            confirming: dict[str, set[str]] = {}  # provider → public IPs (or probe IDs) that confirmed
            names: dict[str, str] = {}
            for (probe_id, k), r in latest.items():
                if k != key:
                    continue
                probe = probes[probe_id]
                answered.add(probe["provider_id"])
                if r["outcome"] == "SUCCESS":
                    confirming.setdefault(probe["provider_id"], set()).add(probe["public_ip"] or probe_id)
                    names[probe["provider_id"]] = probe["provider"]
            matched = _match_providers_to_addresses(confirming)
            per_key[key] = {"score": len(matched), "total": len(answered),
                            "providers": sorted(names[p] for p in matched)}
        for t in targets:
            out[t["fingerprint"]] = dict(per_key[t["key"]])
        return out

    # ================================================================== admin
    def admin_probe_row(self, p: dict, providers: dict, now: float) -> dict:
        """Everything an admin sees about one probe. Admin-only: includes IP and phone."""
        diag = {}
        if p.get("diag_json"):
            try:
                diag = (self.store.loads(p["diag_json"]) or {}).get("summary") or {}
            except ValueError:
                diag = {}
        provider = providers.get(p["provider_id"]) or {}
        last_activity = max((v for v in (p["last_seen"], p["last_job_at"], p["created_at"]) if v), default=None)
        return {
            "id": p["id"],
            "provider_id": p["provider_id"],
            "provider": provider.get("name"),
            "country": provider.get("country"),
            "status": p["status"],
            "lifecycle": self.lifecycle(p),
            "online": self.is_online(p, now),
            "verification": {"state": p["verify_state"], "reason": p["verify_reason"], "at": p["verify_at"],
                             "attempts": p["verify_attempts"]},
            "profile": p["profile"],
            "platform": profiles.PROFILES.get(p["profile"] or "", p["profile"]),
            "version": p["agent_version"],
            "os": " ".join(x for x in (diag.get("distro") or diag.get("os"), diag.get("distro_version")) if x) or None,
            "arch": diag.get("machine") or (p["platform"] or "").split("/")[-1] or None,
            "cpu": diag.get("cpu_model") or None,
            "cpu_cores": diag.get("cpu_cores"),
            "mem_total_kb": diag.get("mem_total_kb"),
            "mem_available_kb": diag.get("mem_available_kb"),
            "storage_free_kb": diag.get("storage_free_kb"),
            "storage_total_kb": diag.get("storage_total_kb"),
            "region": p["region"],
            "asn": p["asn"],
            "public_ip": p["public_ip"],
            "contact_phone": p["contact_phone"],
            "created_at": p["created_at"],
            "last_seen": p["last_seen"],
            "last_heartbeat": p["last_heartbeat"],
            "last_job_at": p["last_job_at"],
            "last_success_at": p["last_success_at"],
            "last_activity": last_activity,
            "jobs_done": p["jobs_done"],
            "jobs_success": p["jobs_success"],
            "jobs_failed": p["jobs_failed"],
            "jobs_error": p["jobs_error"],
            "jobs_expired": p["jobs_expired"],
            "error_count": (p["jobs_error"] or 0) + (p["jobs_expired"] or 0),
            "last_error": p["last_error"],
            "last_error_at": p["last_error_at"],
            "control_ok": p["control_ok"],
            "is_new": p["admin_seen_at"] is None,
        }

    ADMIN_SORTS = {
        "last_heartbeat": "p.last_heartbeat", "created_at": "p.created_at",
        "last_activity": "MAX(COALESCE(p.last_seen,0), COALESCE(p.last_job_at,0), p.created_at)",
        "error_count": "(p.jobs_error + p.jobs_expired)", "jobs_done": "p.jobs_done", "id": "p.id",
    }

    def admin_list_probes(self, *, status: Optional[str] = None, online: Optional[bool] = None,
                          verification: Optional[str] = None, profile: Optional[str] = None,
                          version: Optional[str] = None, provider_id: Optional[str] = None, q: Optional[str] = None,
                          sort: str = "created_at", order: str = "desc", page: int = 1, per_page: int = 25) -> dict:
        now = self.clock()
        where, params = [], []
        for column, value in (("p.status", status), ("p.profile", profile), ("p.agent_version", version),
                              ("p.provider_id", provider_id)):
            if value:
                where.append(f"{column} = ?")
                params.append(value)
        if verification:
            where.append("p.verify_state = ?")
            params.append(verification)
        if online is not None:
            where.append("p.last_seen >= ?" if online else "(p.last_seen IS NULL OR p.last_seen < ?)")
            params.append(now - config.PROBE_OFFLINE_AFTER_SECONDS)
        if q:
            text = q.strip()
            digits = re.sub(r"\D", "", text)
            clauses = ["p.id LIKE ?"]
            params_q = [f"%{text.lower()}%"]
            if len(digits) >= 3:  # phone search matches the digits however they were typed
                clauses.append("REPLACE(p.contact_phone, '+', '') LIKE ?")
                params_q.append(f"%{digits}%")
            where.append(f"({' OR '.join(clauses)})")
            params.extend(params_q)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        column = self.ADMIN_SORTS.get(sort, "p.created_at")
        direction = "ASC" if order == "asc" else "DESC"
        total = self.store.one(f"SELECT COUNT(*) AS n FROM probes p {clause}", params)["n"]
        rows = self.store.all(
            f"SELECT p.*, e.diag_json FROM probes p LEFT JOIN enrollments e ON e.id = p.enrollment_id {clause}"
            f" ORDER BY {column} IS NULL, {column} {direction}, p.id LIMIT ? OFFSET ?",
            [*params, per_page, (page - 1) * per_page])
        providers = {r["id"]: r for r in self.store.all("SELECT * FROM providers")}
        facets = {
            "profiles": [r["profile"] for r in self.store.all("SELECT DISTINCT profile FROM probes WHERE profile IS NOT NULL ORDER BY 1")],
            "versions": [r["v"] for r in self.store.all("SELECT DISTINCT agent_version AS v FROM probes WHERE agent_version IS NOT NULL ORDER BY 1")],
        }
        return {"items": [self.admin_probe_row(p, providers, now) for p in rows], "total": total, "page": page,
                "per_page": per_page, "facets": facets}

    def admin_probe(self, probe_id: str) -> Optional[dict]:
        p = self.store.one("SELECT p.*, e.diag_json, e.info AS enrollment_info, e.client_ip AS enrollment_ip"
                           " FROM probes p LEFT JOIN enrollments e ON e.id = p.enrollment_id WHERE p.id=?", (probe_id,))
        if p is None:
            return None
        providers = {r["id"]: r for r in self.store.all("SELECT * FROM providers WHERE id=?", (p["provider_id"],))}
        row = self.admin_probe_row(p, providers, self.clock())
        row["enrollment"] = {"id": p["enrollment_id"], "info": p["enrollment_info"], "ip": p["enrollment_ip"]}
        row["diagnostics"] = self.store.loads(p["diag_json"]) if p["diag_json"] else None
        row["open_jobs"] = self.store.one("SELECT COUNT(*) AS n FROM jobs WHERE probe_id=? AND status IN ('queued','leased')",
                                          (probe_id,))["n"]
        row["score_contribution"] = self.score_contribution().get(p["provider_id"], 0)
        return row

    def admin_probe_jobs(self, probe_id: str, *, only_errors: bool = False, page: int = 1, per_page: int = 50) -> dict:
        where = "probe_id=?" + (" AND (outcome IN ('FAILED','TIMEOUT','ERROR') OR status='expired')" if only_errors else "")
        total = self.store.one(f"SELECT COUNT(*) AS n FROM jobs WHERE {where}", (probe_id,))["n"]
        rows = self.store.all(
            f"SELECT id, key, type, purpose, status, attempts, created_at, leased_at, completed_at, outcome, error,"
            f" http_status, latency_ms, nonce_hit_at IS NOT NULL AS request_seen FROM jobs WHERE {where}"
            f" ORDER BY created_at DESC LIMIT ? OFFSET ?", (probe_id, per_page, (page - 1) * per_page))
        return {"items": rows, "total": total, "page": page, "per_page": per_page,
                "note": "finished jobs are kept for 24 hours; the counters on the probe are cumulative"}

    def admin_verifications(self, probe_id: str) -> list[dict]:
        rows = self.store.all("SELECT * FROM verifications WHERE probe_id=? ORDER BY id DESC LIMIT 50", (probe_id,))
        for row in rows:
            row["detail"] = self.store.loads(row["detail"]) if row["detail"] else None
            row["reason_text"] = VERIFY_REASONS.get(row["reason"]) if row["reason"] else None
        return rows

    async def set_status(self, probe_id: str, status: str) -> bool:
        """approve (manual override), disable, or pending (enable: re-verified automatically)."""
        now = self.clock()
        if status == "pending":
            changed = self.store.run("UPDATE probes SET status='pending', verify_state='none', verify_reason=NULL,"
                                     " verify_attempts=0 WHERE id=? AND status='disabled'", (probe_id,)) > 0
        elif status == "approved":
            changed = self.store.run("UPDATE probes SET status='approved', verify_state=CASE WHEN verify_state='passed'"
                                     " THEN 'passed' ELSE 'skipped' END, verify_at=COALESCE(verify_at, ?) WHERE id=?",
                                     (now, probe_id)) > 0
        else:
            changed = self.store.run("UPDATE probes SET status=? WHERE id=?", (status, probe_id)) > 0
        if changed and status == "disabled":
            self.store.run("UPDATE jobs SET status='expired', completed_at=? WHERE probe_id=? AND status IN ('queued','leased')",
                           (now, probe_id))
            self.store.run("UPDATE verifications SET state='superseded', reason='superseded', finished_at=?"
                           " WHERE probe_id=? AND state='running'", (now, probe_id))
        if changed and status == "approved":
            await self.ensure_jobs_for_probe(probe_id)
        return changed

    def delete_probe(self, probe_id: str) -> bool:
        """Removes the probe and everything it produced. Its credentials stop working at once."""
        with self.store.transaction() as tx:
            if not tx.execute("SELECT 1 FROM probes WHERE id=?", (probe_id,)).fetchone():
                return False
            tx.execute("DELETE FROM jobs WHERE probe_id=?", (probe_id,))
            tx.execute("DELETE FROM results WHERE probe_id=?", (probe_id,))
            tx.execute("DELETE FROM verifications WHERE probe_id=?", (probe_id,))
            tx.execute("UPDATE enrollments SET probe_id=NULL WHERE probe_id=?", (probe_id,))
            tx.execute("DELETE FROM probes WHERE id=?", (probe_id,))
        self._wakeups.pop(probe_id, None)
        return True

    def set_contact_phone(self, probe_id: str, phone: Optional[str]) -> bool:
        return self.store.run("UPDATE probes SET contact_phone=? WHERE id=?", (phone, probe_id)) > 0

    async def send_test_job(self, probe_id: str) -> Optional[str]:
        """One control check (HTTP_GET to PROBE_CONTROL_URL); its result shows in the probe's jobs."""
        probe = self.store.one("SELECT status FROM probes WHERE id=?", (probe_id,))
        control = await self._control_target()
        if probe is None or probe["status"] == "disabled" or control is None:
            return None
        now = self.clock()
        with self.store.transaction() as tx:
            job_id = _new_id("job_", 10)
            tx.execute(
                "INSERT INTO jobs (id, probe_id, key, type, payload, is_control, status, created_at, expires_at, purpose)"
                " VALUES (?,?,?,?,?,0,'queued',?,?, 'test')",
                (job_id, probe_id, control.key + "|test|" + job_id, control.type, self.store.dumps(control.job_payload()),
                 now - 2, now + config.PROBE_JOB_TTL_SECONDS))
        self._wake(probe_id)
        return job_id

    def mark_seen(self, probe_ids: Optional[list[str]] = None) -> int:
        now = self.clock()
        if probe_ids is None:
            return self.store.run("UPDATE probes SET admin_seen_at=? WHERE admin_seen_at IS NULL", (now,))
        marks = ",".join("?" * len(probe_ids)) or "NULL"
        return self.store.run(f"UPDATE probes SET admin_seen_at=? WHERE admin_seen_at IS NULL AND id IN ({marks})",
                              [now, *probe_ids])

    def unseen_probes(self) -> list[dict]:
        rows = self.store.all("SELECT p.id, p.status, p.verify_state, p.profile, p.created_at, pr.name AS provider"
                              " FROM probes p JOIN providers pr ON pr.id=p.provider_id WHERE p.admin_seen_at IS NULL"
                              " ORDER BY p.created_at DESC LIMIT 50")
        for row in rows:
            row["lifecycle"] = self.lifecycle({**row, "last_seen": None})
        return rows

    def score_contribution(self) -> dict[str, int]:
        """Per provider: how many of the current probe targets it confirms."""
        fps = [r["fingerprint"] for r in self.store.all("SELECT fingerprint FROM targets")]
        counts: dict[str, int] = {}
        names = {r["name"]: r["id"] for r in self.store.all("SELECT id, name FROM providers")}
        for conf in self.confirmations(fps).values():
            for name in conf["providers"]:
                counts[names[name]] = counts.get(names[name], 0) + 1
        return counts

    def admin_providers(self) -> list[dict]:
        now = self.clock()
        contribution = self.score_contribution()
        out = []
        for prov in self.store.all("SELECT * FROM providers ORDER BY name COLLATE NOCASE"):
            probes = self.store.all("SELECT * FROM probes WHERE provider_id=?", (prov["id"],))
            by_status = {s: sum(1 for p in probes if p["status"] == s) for s in ("approved", "pending", "disabled")}
            out.append({
                **self._provider_public(prov),
                "created_at": prov["created_at"],
                "probes_total": len(probes),
                **{f"probes_{k}": v for k, v in by_status.items()},
                "probes_online": sum(1 for p in probes if self.is_online(p, now)),
                "jobs_success": sum(p["jobs_success"] or 0 for p in probes),
                "jobs_failed": sum(p["jobs_failed"] or 0 for p in probes),
                "errors": sum((p["jobs_error"] or 0) + (p["jobs_expired"] or 0) for p in probes),
                "last_heartbeat": max((p["last_heartbeat"] or 0 for p in probes), default=0) or None,
                "last_activity": max((p["last_seen"] or p["created_at"] for p in probes), default=None),
                "score_contribution": contribution.get(prov["id"], 0),
                "public": by_status["approved"] + by_status["disabled"] > 0,
            })
        return out

    def admin_summary(self) -> dict:
        now = self.clock()
        probes = self.store.all("SELECT status, verify_state, last_seen FROM probes")
        return {
            "total": len(probes),
            "approved": sum(1 for p in probes if p["status"] == "approved"),
            "pending": sum(1 for p in probes if p["status"] == "pending"),
            "disabled": sum(1 for p in probes if p["status"] == "disabled"),
            "online": sum(1 for p in probes if self.is_online(p, now)),
            "online_approved": sum(1 for p in probes if p["status"] == "approved" and self.is_online(p, now)),
            "verifying": sum(1 for p in probes if p["verify_state"] == "running"),
            "verification_failed": sum(1 for p in probes if p["status"] == "pending" and p["verify_state"] == "failed"),
            "new": self.store.one("SELECT COUNT(*) AS n FROM probes WHERE admin_seen_at IS NULL")["n"],
            "open_jobs": self.store.one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','leased')")["n"],
        }

    # Kept for the original admin endpoint and its tests.
    def admin_probes(self) -> list[dict]:
        return self.admin_list_probes(per_page=10_000)["items"]


def _match_providers_to_addresses(confirming: dict[str, set[str]]) -> list[str]:
    """Largest set of providers that can each be backed by a *different* address
    (bipartite matching; the sets are tiny). Returns the matched provider IDs."""
    owner: dict[str, str] = {}

    def assign(provider: str, seen: set[str]) -> bool:
        for address in confirming[provider]:
            if address in seen:
                continue
            seen.add(address)
            if address not in owner or assign(owner[address], seen):
                owner[address] = provider
                return True
        return False

    for provider in sorted(confirming):
        assign(provider, set())
    return sorted(set(owner.values()))


def _default_manifest():
    from app.probes.releases import manifest

    return manifest(config.PROBE_DIST_DIR)


# The database opens lazily on first use, so importing this module has no side effects.
probe_service = ProbeService(Store(config.PROBE_DB_PATH), _default_manifest)
