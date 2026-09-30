"""/api/admin/*: everything the admin panel does. Every route except session login depends on
require_admin (tests enumerate the routes to prove it). Responses are admin-only data:
probe IPs and contact phones appear here and nowhere else.
"""
import asyncio
import os
import resource
import shutil
import sys
import time
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import config, db, events
from app.admin import auth, settings as runtime_settings
from app.api.rate_limit import client_ip
from app.probes import service as probe_module
from app.probes.phone import InvalidPhone, normalize_phone
from app.probes.releases import manifest
from app.services import ranking, safe_fetch, sources
from app.services.scan_history import scan_history
from app.services.scanner import extract_source_nodes, usable_node

STARTED_AT = time.time()
APP_VERSION = "3.1.0"

public_router = APIRouter()  # login only
router = APIRouter(dependencies=[Depends(auth.require_admin)])


def svc():
    return probe_module.probe_service


def scans():
    from app.services.scan_service import scan_service

    return scan_service


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _probe_or_404(probe_id: str) -> dict:
    if not probe_module.PROBE_ID_RE.match(probe_id):
        raise HTTPException(status_code=404, detail="No such probe")
    probe = svc().admin_probe(probe_id)
    if probe is None:
        raise HTTPException(status_code=404, detail="No such probe")
    return probe


def _audit(kind: str, message: str, **kwargs) -> None:
    events.record("admin", kind, message, **kwargs)


# ====================================================================== session
class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=512)


def _admin_enabled() -> None:
    """Runs before body validation: with the admin API off, login is indistinguishable from any 404."""
    if not config.ADMIN_TOKEN:
        raise HTTPException(status_code=404)


@public_router.post("/admin/session", dependencies=[Depends(_admin_enabled)])
async def login(body: LoginRequest, request: Request, response: Response):
    """Exchange ADMIN_TOKEN for an HttpOnly session cookie. The token is not stored anywhere."""
    _no_store(response)
    auth.login_limiter.check(client_ip(request))
    if request.headers.get(auth.CSRF_HEADER) != "1" or not auth._origin_allowed(request):
        raise HTTPException(status_code=403, detail="Missing admin request header or foreign origin")
    if not auth.token_matches(body.token):
        events.record("auth", "admin_login_failed", "Admin login with a wrong token", severity="warning",
                      data={"ip": client_ip(request)})
        raise HTTPException(status_code=401, detail="Wrong admin token")
    session = auth.create_session(request, response)
    events.record("auth", "admin_login", "Admin signed in", data={"ip": client_ip(request)})
    return session


@router.get("/admin/session")
async def session_info(response: Response, how: str = Depends(auth.require_admin)):
    _no_store(response)
    return {"authenticated": True, "via": how}


@router.delete("/admin/session")
async def logout(request: Request, response: Response):
    auth.end_session(request, response)
    return {"authenticated": False}


# ====================================================================== dashboard & notifications
@router.get("/admin/dashboard")
async def dashboard(response: Response):
    _no_store(response)
    service = scans()
    all_sources = sources.list_all()
    day_ago = time.time() - 86400
    last = scan_history.last()
    last_ok = scan_history.last("completed")
    errors_today = db.store().one("SELECT COUNT(*) AS n FROM events WHERE severity='error' AND ts>=?", (day_ago,))["n"]
    warnings_today = db.store().one("SELECT COUNT(*) AS n FROM events WHERE severity='warning' AND ts>=?", (day_ago,))["n"]
    entry = service.entry()
    return {
        "probes": svc().admin_summary(),
        "sources": {
            "total": len(all_sources),
            "enabled": sum(1 for s in all_sources if s["enabled"]),
            "healthy": sum(1 for s in all_sources if s["enabled"] and s["last_status"] == "ok"),
            "failed": sum(1 for s in all_sources if s["enabled"] and s["last_status"] == "error"),
            "never_fetched": sum(1 for s in all_sources if s["enabled"] and s["last_status"] == "never"),
        },
        "scanner": {**_scanner_state(), "last_scan": last, "last_success": last_ok},
        "cache": {"state": service.cache_state(), "count": entry.get("count") if entry else 0,
                  "created_at": entry.get("created_ts") if entry else None,
                  "expires_at": entry.get("expires_ts") if entry else None,
                  "candidates": len(ranking.candidates(entry))},
        "errors_today": errors_today,
        "warnings_today": warnings_today,
        "recent_events": events.query(per_page=12)["items"],
    }


@router.get("/admin/notifications")
async def notifications(response: Response, after: Optional[int] = Query(default=None, ge=0)):
    """Cheap enough to poll: one indexed query for new events plus the unread probes.
    `latest_id` is the cursor the panel keeps, so a reload never replays old sounds; without
    `after` (a panel's very first poll) nothing counts as new."""
    _no_store(response)
    store = db.store()
    latest = store.one("SELECT MAX(id) AS id FROM events WHERE kind='probe_registered'")["id"] or 0
    fresh = store.all("SELECT id, ts, probe_id, data FROM events WHERE kind='probe_registered' AND id>? ORDER BY id LIMIT 20",
                      (after,)) if after is not None else []
    for row in fresh:
        row["data"] = store.loads(row["data"]) if row["data"] else {}
    unread = svc().unseen_probes()
    return {"latest_id": latest, "new": fresh, "unread_count": len(unread), "unread": unread}


class ReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    probe_ids: Optional[list[str]] = Field(default=None, max_length=200)
    all: bool = False


@router.post("/admin/notifications/read")
async def notifications_read(body: ReadRequest):
    if body.all:
        return {"marked": svc().mark_seen(None)}
    ids = [p for p in (body.probe_ids or []) if probe_module.PROBE_ID_RE.match(p)]
    return {"marked": svc().mark_seen(ids) if ids else 0}


# ====================================================================== probes
@router.get("/admin/probes")
async def list_probes(
    response: Response,
    status: Optional[Literal["pending", "approved", "disabled"]] = None,
    online: Optional[bool] = None,
    verification: Optional[Literal["none", "running", "passed", "failed", "skipped"]] = None,
    profile: Optional[str] = Query(default=None, max_length=32),
    version: Optional[str] = Query(default=None, max_length=40),
    provider_id: Optional[str] = Query(default=None, max_length=40),
    q: Optional[str] = Query(default=None, max_length=64),
    sort: Literal["last_heartbeat", "created_at", "last_activity", "error_count", "jobs_done", "id"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(default=1, ge=1, le=10_000),
    per_page: int = Query(default=25, ge=1, le=200),
):
    _no_store(response)
    result = svc().admin_list_probes(status=status, online=online, verification=verification, profile=profile,
                                     version=version, provider_id=provider_id, q=q, sort=sort, order=order,
                                     page=page, per_page=per_page)
    # "probes" keeps the original response shape working for scripts.
    return {**result, "probes": result["items"], "stats": svc().stats}


@router.get("/admin/probes/{probe_id}")
async def probe_detail(probe_id: str, response: Response):
    _no_store(response)
    return _probe_or_404(probe_id)


@router.get("/admin/probes/{probe_id}/jobs")
async def probe_jobs(probe_id: str, response: Response, errors: bool = False,
                     page: int = Query(default=1, ge=1), per_page: int = Query(default=50, ge=1, le=200)):
    _no_store(response)
    _probe_or_404(probe_id)
    return svc().admin_probe_jobs(probe_id, only_errors=errors, page=page, per_page=per_page)


@router.get("/admin/probes/{probe_id}/verifications")
async def probe_verifications(probe_id: str, response: Response):
    _no_store(response)
    _probe_or_404(probe_id)
    return {"items": svc().admin_verifications(probe_id)}


@router.post("/admin/probes/{probe_id}/{action}")
async def probe_action(probe_id: str, action: Literal["approve", "disable", "enable", "verify", "test"]):
    probe = _probe_or_404(probe_id)
    if action in ("approve", "disable", "enable"):
        status = {"approve": "approved", "disable": "disabled", "enable": "pending"}[action]
        if action == "enable" and probe["status"] != "disabled":
            raise HTTPException(status_code=409, detail="Only a disabled probe can be enabled")
        await svc().set_status(probe_id, status)
        _audit(f"probe_{action}", f"Admin {action}d probe {probe_id}", probe_id=probe_id)
        return {"probe_id": probe_id, "status": status}
    if action == "verify":
        if probe["status"] == "disabled":
            raise HTTPException(status_code=409, detail="Enable the probe first")
        verification_id = await svc().start_verification(probe_id, "admin")
        _audit("probe_verify", f"Admin re-ran verification of probe {probe_id}", probe_id=probe_id)
        return {"probe_id": probe_id, "verification_id": verification_id}
    job_id = await svc().send_test_job(probe_id)
    if job_id is None:
        raise HTTPException(status_code=409, detail="A test job cannot be sent to this probe now")
    _audit("probe_test", f"Admin sent a test job to probe {probe_id}", probe_id=probe_id)
    return {"probe_id": probe_id, "job_id": job_id}


class ProbePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact_phone: Optional[str] = Field(default=None, max_length=32)

    @field_validator("contact_phone")
    @classmethod
    def _phone(cls, v):
        try:
            return normalize_phone(v)
        except InvalidPhone as exc:
            raise ValueError(str(exc)) from None


@router.patch("/admin/probes/{probe_id}")
async def probe_patch(probe_id: str, body: ProbePatch):
    _probe_or_404(probe_id)
    svc().set_contact_phone(probe_id, body.contact_phone)
    _audit("probe_contact_changed", f"Admin changed the contact of probe {probe_id}", probe_id=probe_id)
    return _probe_or_404(probe_id)


@router.delete("/admin/probes/{probe_id}")
async def probe_delete(probe_id: str):
    _probe_or_404(probe_id)
    svc().delete_probe(probe_id)
    _audit("probe_deleted", f"Admin deleted probe {probe_id}", probe_id=probe_id, severity="warning")
    return {"deleted": probe_id}


@router.post("/admin/probes-round")
async def admin_probe_round():
    """Re-run the probe round for the current main cache (e.g. after approving probes)."""
    entry = scans().entry()
    if not entry:
        raise HTTPException(status_code=409, detail="No scan results yet")
    summary = await svc().schedule_scan(entry)
    scan_history.probe_round(entry.get("scan_id"), summary)
    return summary


@router.get("/admin/providers")
async def providers(response: Response):
    _no_store(response)
    return {"items": svc().admin_providers()}


# ====================================================================== sources
class SourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, max_length=120)
    url: Optional[str] = Field(default=None, max_length=4096)
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    refresh_seconds: Optional[int] = None
    timeout_seconds: Optional[float] = None


def _source_or_404(source_id: int) -> dict:
    row = sources.get(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such source")
    return row


@router.get("/admin/sources")
async def list_sources(response: Response):
    _no_store(response)
    return {"items": sources.list_all()}


@router.get("/admin/sources/{source_id}")
async def source_detail(source_id: int, response: Response):
    _no_store(response)
    return _source_or_404(source_id)


@router.post("/admin/sources", status_code=201)
async def create_source(body: SourceBody):
    data = body.model_dump(exclude_unset=True)
    if not data.get("url"):
        raise HTTPException(status_code=422, detail="url is required")
    try:
        row = await sources.create(data)
    except safe_fetch.UnsafeURL as exc:
        raise HTTPException(status_code=422, detail=f"URL refused: {exc}") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    _audit("source_created", f"Admin added source “{row['name']}”", source_id=row["id"])
    return row


@router.patch("/admin/sources/{source_id}")
async def update_source(source_id: int, body: SourceBody):
    _source_or_404(source_id)
    data = body.model_dump(exclude_unset=True)
    try:
        row = await sources.update(source_id, data)
    except safe_fetch.UnsafeURL as exc:
        raise HTTPException(status_code=422, detail=f"URL refused: {exc}") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    _audit("source_updated", f"Admin changed source “{row['name']}”: {', '.join(sorted(data))}", source_id=source_id)
    return row


@router.delete("/admin/sources/{source_id}")
async def delete_source(source_id: int):
    row = _source_or_404(source_id)
    sources.delete(source_id)
    _audit("source_deleted", f"Admin deleted source “{row['name']}”", source_id=source_id, severity="warning")
    return {"deleted": source_id}


def _count_links(text: str) -> dict:
    links = extract_source_nodes(text)
    seen, valid, sample = set(), 0, []
    for link in links:
        node = usable_node(link)
        if node is None:
            continue
        valid += 1
        if node.fingerprint not in seen:
            seen.add(node.fingerprint)
            if len(sample) < 5:
                sample.append(f"{node.address}:{node.port} {node.network}/{node.security}")
    return {"fetched": len(links), "valid": valid, "unique": len(seen), "sample": sample}


async def _fetch_source(row: dict, *, store_result: bool) -> dict:
    if row["kind"] == "vless":
        counts = _count_links(row["url"])
        outcome = {"ok": counts["valid"] == 1, "error": "" if counts["valid"] else "the link cannot be tested",
                   **counts, "bytes": len(row["url"]), "status": None, "duration_ms": 0, "redirects": []}
    else:
        spec = next((s for s in sources.enabled_specs() if s.id == row["id"]), None) or sources.SourceSpec(
            row["id"], row["name"], row["url"], row["kind"], float(row["timeout_seconds"] or config.SOURCE_TIMEOUT),
            int(row["refresh_seconds"] or 0), row["last_success_at"])
        text, fetched, _ = await sources.fetch_body(spec, use_cache=False)
        counts = _count_links(text) if fetched.ok else {"fetched": 0, "valid": 0, "unique": 0, "sample": []}
        outcome = {"ok": fetched.ok, "error": fetched.error, **counts, "bytes": fetched.bytes, "status": fetched.status,
                   "duration_ms": fetched.duration_ms, "redirects": fetched.redirects}
    if store_result:
        sources.record(row["id"], ok=outcome["ok"], error=outcome["error"],
                       fetched=outcome["fetched"] if outcome["ok"] else None,
                       valid=outcome["valid"] if outcome["ok"] else None,
                       unique=outcome["unique"] if outcome["ok"] else None,
                       detail={k: outcome[k] for k in ("bytes", "status", "duration_ms", "sample", "redirects")})
    return outcome


@router.post("/admin/sources/{source_id}/test")
async def test_source(source_id: int):
    """Download and parse now; nothing is stored."""
    return await _fetch_source(_source_or_404(source_id), store_result=False)


@router.post("/admin/sources/{source_id}/fetch")
async def fetch_source(source_id: int):
    """Download and parse now, and record the outcome as the source's last result."""
    outcome = await _fetch_source(_source_or_404(source_id), store_result=True)
    return {**outcome, "source": sources.get(source_id)}


class SourceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=4096)


@router.post("/admin/sources-check")
async def check_source_url(body: SourceCheck):
    """Validate a URL before saving it (the form's "Test" on a new source)."""
    try:
        sources.validate_fields({"url": body.url})
        await sources.check_reachable_safely(body.url)
    except (safe_fetch.UnsafeURL, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"URL refused: {exc}") from None
    row = {"id": 0, "name": "", "url": body.url, "kind": sources.kind_of(body.url), "timeout_seconds": None,
           "refresh_seconds": 0, "last_success_at": None}
    return await _fetch_source(row, store_result=False)


# ====================================================================== scanner, scans, cache
def _scanner_state() -> dict:
    service = scans()
    live = service.scanner.get_status()
    running = service.running()
    return {"state": "scanning" if any(r["state"] == "running" for r in running) else
            ("queued" if running else "idle"), "live": live, "queue": running,
            "last_error": service.last_error()}


@router.get("/admin/scanner")
async def scanner_status(response: Response):
    _no_store(response)
    current = scan_history.last("running")
    return {**_scanner_state(), "current_scan": current, "last_scan": scan_history.last(),
            "cache": scans().status(), "stats": scans().stats,
            "settings": {k: getattr(config, k) for k in ("CANDIDATE_COUNT", "TOP_COUNT", "BATCH_SIZE", "WORKERS",
                                                          "TIMEOUT", "MAX_NODES", "CACHE_TTL_SECONDS")}}


@router.post("/admin/scanner/{action}")
async def scanner_action(action: Literal["start", "run", "cancel", "restart"]):
    """start: scan only if the cache is stale · run: scan now ignoring the cache · cancel ·
    restart: cancel, then scan now. Scans stay one at a time (the scanner's lock)."""
    service = scans()
    if action == "cancel":
        cancelled = service.cancel()
        if not cancelled:
            raise HTTPException(status_code=409, detail="No scan is running")
        _audit("scan_cancel", f"Admin cancelled scans: {', '.join(cancelled)}", severity="warning")
        return {"cancelled": cancelled}
    if action == "restart":
        cancelled = service.cancel()
        for _ in range(200):  # the cancelled scan stops Xray and releases the lock first
            if not service.running():
                break
            await asyncio.sleep(0.05)
        outcome = service.request_scan(force=True, trigger="admin")
        _audit("scan_restart", "Admin restarted the scan")
        return {"cancelled": cancelled, "outcome": outcome}
    outcome = service.request_scan(force=action == "run", trigger="admin")
    _audit(f"scan_{action}", f"Admin requested a scan ({action}): {outcome}")
    return {"outcome": outcome}


# Original endpoints, kept for scripts.
@router.get("/admin/stats")
async def admin_stats():
    return {**scans().status(), "stats": scans().stats, "last_error": scans().last_error()}


@router.post("/admin/scan")
async def admin_force_scan():
    """Rescan now, ignoring the cache."""
    return {"outcome": scans().request_scan(force=True, trigger="admin")}


@router.get("/admin/scans")
async def list_scans(response: Response, status: Optional[Literal["running", "completed", "failed", "cancelled"]] = None,
                     query: Optional[str] = Query(default=None, pattern=r"^(all|[A-Z]{2})$"),
                     page: int = Query(default=1, ge=1), per_page: int = Query(default=25, ge=1, le=200)):
    _no_store(response)
    return scan_history.list(status=status, query=query, page=page, per_page=per_page)


@router.get("/admin/scans/{scan_id}")
async def scan_detail(scan_id: int, response: Response):
    _no_store(response)
    row = scan_history.get(scan_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such scan")
    return row


@router.get("/admin/cache")
async def cache_status(response: Response):
    """Cache files plus the live ranking of every candidate of the main cache: the first
    TOP_COUNT rows are exactly what the public API serves."""
    _no_store(response)
    service = scans()
    entry = service.entry()
    nodes = ranking.candidates(entry)
    ranked = ranking.rank(nodes, svc().confirmations([n["fingerprint"] for n in nodes])) if nodes else []
    return {"ttl_seconds": service.ttl_seconds, "files": service.cache_files(),
            "main": service.cache_meta(), "last_scan": scan_history.last("completed"),
            "use_probe_score": config.RANKING_USE_PROBE_SCORE, "top_count": config.TOP_COUNT,
            "ranking": [{"rank": i, "in_top": i <= config.TOP_COUNT, "fingerprint": n["fingerprint"],
                         "address": n["address"], "port": n["port"], "label": n.get("name", "")[:60],
                         "probe_score": n["probe_score"], "probe_total": n["probe_total"],
                         "probe_providers": n["probe_providers"], "backend_rank": n["backend_rank"],
                         "latency_ms": n.get("latency_ms"), "speed_mbps": n.get("speed_mbps")}
                        for i, n in enumerate(ranked, 1)]}


@router.post("/admin/cache/{action}")
async def cache_action(action: Literal["clear", "rebuild", "refresh"]):
    """clear: delete every cache file · rebuild: clear, then scan now · refresh: re-read the
    files from disk and re-rank (the ranking itself is always computed live)."""
    service = scans()
    if action == "refresh":
        service.reload()
        return {"status": "refreshed", "files": service.cache_files()}
    service.clear()
    _audit(f"cache_{action}", f"Admin {'cleared' if action == 'clear' else 'rebuilt'} the scan cache", severity="warning")
    if action == "clear":
        return {"status": "cleared"}
    return {"status": "rebuilding", "outcome": service.request_scan(force=True, trigger="admin")}


# ====================================================================== system
def _memory() -> dict:
    out = {"rss_bytes": None, "max_rss_bytes": None, "total_bytes": None, "available_bytes": None}
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    out["rss_bytes"] = int(line.split()[1]) * 1024
        with open("/proc/meminfo") as fh:
            info = {line.split(":")[0]: int(line.split()[1]) * 1024 for line in fh if line.split()[1:2]}
        out["total_bytes"], out["available_bytes"] = info.get("MemTotal"), info.get("MemAvailable")
    except OSError:
        pass
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    out["max_rss_bytes"] = usage if sys.platform == "darwin" else usage * 1024
    return out


@router.get("/admin/health")
async def health(response: Response):
    _no_store(response)
    now = time.time()
    components = {}

    def put(name: str, status: str, summary: str, **extra):
        components[name] = {"status": status, "summary": summary, **extra}

    put("backend", "ok", f"up {int(now - STARTED_AT)} s", uptime_seconds=int(now - STARTED_AT), pid=os.getpid())
    try:
        db.store().one("SELECT 1")
        integrity = db.store().one("PRAGMA quick_check")
        ok = list(integrity.values())[0] == "ok"
        put("database", "ok" if ok else "error", "SQLite reachable" if ok else "integrity check failed",
            path=str(db.store().path.name), schema=db.store().one("PRAGMA user_version")["user_version"])
    except Exception as exc:
        put("database", "error", f"{type(exc).__name__}")

    service = scans()
    last_ok = scan_history.last("completed")
    last = scan_history.last()
    state = _scanner_state()
    if service.last_error() and not state["queue"]:
        put("scanner", "error", f"last scan failed: {service.last_error()}", state=state["state"])
    elif last_ok and now - last_ok["finished_at"] > config.CACHE_TTL_SECONDS * 2:
        put("scanner", "warning", "no successful scan for over two cache lifetimes", state=state["state"])
    else:
        put("scanner", "ok", state["state"], state=state["state"])
    # Only look: ensure_xray_binary() would try to download Xray.
    xray = os.path.exists(config.XRAY_BINARY) or bool(shutil.which("xray"))
    if not xray:
        put("scanner", "error", "Xray binary missing", state=state["state"])

    summary = svc().admin_summary()
    if summary["approved"] == 0:
        put("probes", "warning", "no approved probes: ranking uses backend checks only", **summary)
    elif summary["online_approved"] == 0:
        put("probes", "warning", "no approved probe is online", **summary)
    else:
        put("probes", "ok", f"{summary['online_approved']} of {summary['approved']} approved probes online", **summary)

    all_sources = [s for s in sources.list_all() if s["enabled"]]
    failed = [s for s in all_sources if s["last_status"] == "error"]
    if not all_sources:
        put("sources", "error", "no enabled sources")
    elif len(failed) == len(all_sources):
        put("sources", "error", "every source failed", failed=len(failed))
    elif failed:
        put("sources", "warning", f"{len(failed)} of {len(all_sources)} sources failing", failed=len(failed))
    else:
        put("sources", "ok", f"{len(all_sources)} sources enabled")

    cache_state = service.cache_state()
    put("cache", {"fresh": "ok", "stale": "warning", "empty": "warning"}[cache_state], cache_state)

    disk = shutil.disk_usage(db.store().path.parent)
    used = disk.used / disk.total if disk.total else 0
    put("disk", "error" if used > 0.95 else "warning" if used > 0.85 else "ok", f"{used:.0%} used",
        total_bytes=disk.total, free_bytes=disk.free)

    order = {"ok": 0, "warning": 1, "error": 2}
    overall = max((c["status"] for c in components.values()), key=order.get)
    return {
        "status": overall,
        "components": components,
        "versions": {"backend": APP_VERSION, "scanner": config.SCANNER_VERSION,
                     "probe_agent": manifest(config.PROBE_DIST_DIR).version, "python": sys.version.split()[0],
                     "xray": bool(xray)},
        "uptime_seconds": int(now - STARTED_AT),
        "memory": _memory(),
        "last_successful_scan": last_ok,
        "last_scan": last,
        "recent_errors": events.query(severity="error", per_page=10)["items"],
    }


@router.get("/admin/events")
async def list_events(
    response: Response,
    component: Optional[Literal[events.COMPONENTS]] = None,  # type: ignore[valid-type]
    severity: Optional[Literal["info", "warning", "error"]] = None,
    kind: Optional[str] = Query(default=None, max_length=40),
    probe_id: Optional[str] = Query(default=None, max_length=24),
    source_id: Optional[int] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
    q: Optional[str] = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1), per_page: int = Query(default=50, ge=1, le=200),
):
    _no_store(response)
    return events.query(component=component, severity=severity, kind=kind, probe_id=probe_id, source_id=source_id,
                        since=since, until=until, search=q, page=page, per_page=per_page)


@router.get("/admin/settings")
async def get_settings(response: Response):
    _no_store(response)
    return {"items": runtime_settings.view()}


@router.put("/admin/settings")
async def put_settings(changes: dict):
    if not changes or len(changes) > len(runtime_settings.SPECS):
        raise HTTPException(status_code=422, detail="nothing to change")
    try:
        return {"items": runtime_settings.update(changes)}
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=f"unknown or protected setting: {exc.args[0]}") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.delete("/admin/settings/{key}")
async def reset_setting(key: str):
    try:
        return {"items": runtime_settings.reset(key)}
    except KeyError:
        raise HTTPException(status_code=404, detail="No such setting") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
