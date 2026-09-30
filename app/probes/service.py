"""Probe coordination: enrollment, credentials, heartbeats, the job queue and aggregation.

Flow:
  website form → enrollment (one-time token) → installer diagnostics (SUPPORTED/UNSUPPORTED)
  → agent registers with the token → probe ID + secret → heartbeats, job polling, results.

After every successful full scan, the best PROBE_TARGET_COUNT configs become targets;
every online probe gets one job per unique target it has no fresh result for. Results
are kept per probe and aggregated per provider when the API shows them.
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

from app import config
from app.probes import profiles
from app.probes.releases import Manifest
from app.probes.store import Store
from app.probes.targets import Target, TargetRejected, control_target, target_for_node

logger = logging.getLogger("skat.probes")

Outcome = Literal["SUCCESS", "FAILED", "TIMEOUT", "ERROR"]
OUTCOMES = ("SUCCESS", "FAILED", "TIMEOUT", "ERROR")
# What the site shows per config and provider. Never conflated:
State = Literal["SUCCESS", "FAILED", "TIMEOUT", "CHECKING", "NO_PROBE", "OFFLINE", "NOT_TESTED"]

PROBE_ID_RE = re.compile(r"^prb_[0-9a-f]{16}$")
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
        self.stats = {"jobs_created": 0, "jobs_reused": 0, "results_accepted": 0, "results_rejected": 0}

    # ================================================================== enrollment
    def create_enrollment(self, *, name: str, country: str, region: str = "", asn: Optional[int] = None,
                          info: str = "", client_ip: str = "") -> dict:
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
                "INSERT INTO enrollments (id, provider_id, token_hash, region, asn, info, client_ip, created_at, expires_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (enrollment_id, provider_id, _hash(token), region or None, asn, info or None, client_ip, now, expires),
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
            state = ("online" if self.is_online(probe) else "offline") if probe["last_seen"] else "registered"
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
        }

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
                " platform, profile, agent_version, public_ip, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (probe_id, row["provider_id"], row["id"], status, _hash(secret), now, row["region"], row["asn"],
                 platform, profile, agent_version, public_ip, now),
            )
            tx.execute("UPDATE enrollments SET used_at=?, probe_id=? WHERE id=?", (now, probe_id, row["id"]))
        logger.info("probe %s registered (%s, %s, %s)", probe_id, status, profile, agent_version)
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
            await self.ensure_jobs_for_probe(probe["id"])

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
        created = 0
        for probe in self.store.all("SELECT * FROM probes WHERE status != 'disabled'"):
            if self.is_online(probe):
                created += await self._enqueue(probe["id"], targets)
        logger.info("probe round: %d targets (%d unique), %d jobs created", len(targets), len({t.key for t in targets}), created)
        return {"targets": len(targets), "rejected": rejected, "jobs_created": created}

    def current_targets(self) -> list[Target]:
        out = []
        for row in self.store.all("SELECT payload FROM targets WHERE payload IS NOT NULL ORDER BY rank"):
            try:
                out.append(Target.from_payload(self.store.loads(row["payload"])))
            except (TargetRejected, KeyError, ValueError):
                continue
        return out

    async def ensure_jobs_for_probe(self, probe_id: str) -> int:
        """A probe that (re)appears gets the current targets it has no fresh result for."""
        return await self._enqueue(probe_id, self.current_targets())

    async def _enqueue(self, probe_id: str, targets: Iterable[Target]) -> int:
        now = self.clock()
        unique = {t.key: t for t in targets}
        created = reused = 0
        probe = self.store.one("SELECT control_at FROM probes WHERE id=?", (probe_id,))
        control = await self._control_target() if unique else None
        with self.store.transaction() as tx:
            if control and (not probe["control_at"] or now - probe["control_at"] > CONTROL_MAX_AGE_SECONDS):
                created += self._insert_job(tx, probe_id, control, now, is_control=True)
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

    def _insert_job(self, tx, probe_id: str, target: Target, now: float, is_control: bool = False) -> int:
        cursor = tx.execute(
            "INSERT OR IGNORE INTO jobs (id, probe_id, key, type, payload, is_control, status, created_at, expires_at)"
            " VALUES (?,?,?,?,?,?, 'queued', ?, ?)",
            (_new_id("job_", 10), probe_id, target.key, target.type, self.store.dumps(target.job_payload()),
             int(is_control), now - (1 if is_control else 0), now + config.PROBE_JOB_TTL_SECONDS),
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
                " ORDER BY is_control DESC, created_at LIMIT ?",
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
        job, never from the probe, so a probe cannot inject endpoints or keys."""
        now = self.clock()
        accepted, rejected = 0, []
        control_failed_recently = (
            probe.get("control_ok") == 0 and probe.get("control_at") and now - probe["control_at"] < CONTROL_MAX_AGE_SECONDS
        )
        with self.store.transaction() as tx:
            for item in results:
                job = tx.execute(
                    "SELECT * FROM jobs WHERE id=? AND probe_id=? AND status IN ('queued','leased')",
                    (item["job_id"], probe["id"]),
                ).fetchone()
                if job is None:
                    rejected.append(item["job_id"])
                    continue
                tx.execute("UPDATE jobs SET status='done', completed_at=? WHERE id=?", (now, job["id"]))
                outcome = item["outcome"]
                if job["is_control"]:
                    ok = outcome == "SUCCESS"
                    tx.execute("UPDATE probes SET control_ok=?, control_at=? WHERE id=?", (int(ok), now, probe["id"]))
                    control_failed_recently = not ok
                    accepted += 1
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
                accepted += 1
        self.stats["results_accepted"] += accepted
        self.stats["results_rejected"] += len(rejected)
        self.touch(probe["id"])
        return {"accepted": accepted, "rejected": rejected}

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
            expired = tx.execute(
                "UPDATE jobs SET status='expired', completed_at=?"
                " WHERE (status='leased' AND lease_expires<?) OR (status='queued' AND expires_at<=?)",
                (now, now, now),
            ).rowcount
            tx.execute("DELETE FROM jobs WHERE status IN ('done','expired') AND completed_at<?", (now - CLEANUP_KEEP_SECONDS,))
            tx.execute("DELETE FROM results WHERE expires_at<?", (now - CLEANUP_KEEP_SECONDS,))
            tx.execute("DELETE FROM enrollments WHERE probe_id IS NULL AND expires_at<?", (now - CLEANUP_KEEP_SECONDS,))
        if retried:
            for probe_id in list(self._wakeups):
                self._wake(probe_id)
        return {"retried": retried, "expired": expired}

    async def run_forever(self, interval: float = 15.0) -> None:
        while True:
            try:
                self.maintain()
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

    # ================================================================== admin
    def admin_probes(self) -> list[dict]:
        providers = {r["id"]: r for r in self.store.all("SELECT * FROM providers")}
        return [
            {**self._probe_public(p, providers.get(p["provider_id"])), "public_ip": p["public_ip"],
             "provider_id": p["provider_id"], "control_ok": p["control_ok"], "created_at": p["created_at"]}
            for p in self.store.all("SELECT * FROM probes ORDER BY created_at DESC")
        ]

    async def set_status(self, probe_id: str, status: str) -> bool:
        changed = self.store.run("UPDATE probes SET status=? WHERE id=?", (status, probe_id)) > 0
        if changed and status == "disabled":
            self.store.run("UPDATE jobs SET status='expired', completed_at=? WHERE probe_id=? AND status IN ('queued','leased')",
                           (self.clock(), probe_id))
        if changed and status == "approved":
            await self.ensure_jobs_for_probe(probe_id)
        return changed


def _default_manifest():
    from app.probes.releases import manifest

    return manifest(config.PROBE_DIST_DIR)


# The database opens lazily on first use, so importing this module has no side effects.
probe_service = ProbeService(Store(config.PROBE_DB_PATH), _default_manifest)
