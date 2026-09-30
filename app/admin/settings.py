"""Runtime settings the admin panel may change, stored in SQLite and applied over app.config.

Only keys listed in SPECS exist here, each with a type and bounds. Secrets and anything
that decides where the server listens or what it trusts (ADMIN_TOKEN, ALLOWED_ORIGINS,
TRUST_PROXY_HEADERS, PUBLIC_API_URL, paths...) are deliberately absent: they stay in .env.
Environment values are the defaults; a stored override wins until it is reset.
"""
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app import config, db, events


@dataclass(frozen=True)
class Spec:
    key: str
    type: type
    category: str
    label: str
    min: Optional[float] = None
    max: Optional[float] = None
    restart: bool = False  # only read at startup
    apply: Optional[Callable[[Any], None]] = None


def _scan_service():
    from app.services.scan_service import scan_service

    return scan_service


def _limiter(module: str, name: str) -> Callable[[Any], None]:
    def apply(value):
        import importlib

        setattr(getattr(importlib.import_module(module), name), "limit", int(value))

    return apply


SPECS: dict[str, Spec] = {s.key: s for s in (
    # Scanner
    Spec("CANDIDATE_COUNT", int, "scanner", "Working configs to collect before deep checks", 1, 200),
    Spec("TOP_COUNT", int, "ranking", "Configs in the public top", 1, 50),
    Spec("MAX_NODES", int, "scanner", "Unique configs considered per scan", 100, 200_000),
    Spec("BATCH_SIZE", int, "scanner", "Configs per Xray process", 10, 2000),
    Spec("WORKERS", int, "scanner", "Concurrent qualification checks", 1, 2000),
    Spec("TIMEOUT", float, "scanner", "Single request through a config, seconds", 1, 30),
    Spec("DEEP_ATTEMPTS", int, "scanner", "Latency checks per candidate", 1, 20),
    Spec("DEEP_MIN_SUCCESS", int, "scanner", "Latency checks that must pass", 1, 20),
    # Sources
    Spec("SOURCE_CONCURRENCY", int, "sources", "Sources downloaded at once", 1, 100),
    Spec("SOURCE_TIMEOUT", float, "sources", "Default source download timeout, seconds", 1, 300),
    Spec("SOURCE_MAX_BYTES", int, "sources", "Largest accepted source, bytes", 1024 * 1024, 256 * 1024 * 1024),
    Spec("SOURCE_MAX_REDIRECTS", int, "sources", "Redirects followed per source", 0, 10),
    # Cache & public API
    Spec("CACHE_TTL_SECONDS", int, "cache", "Scan cache lifetime, seconds", 600, 7 * 86400,
         apply=lambda v: _scan_service().set_ttl(int(v))),
    Spec("REFRESH_COOLDOWN_SECONDS", int, "cache", "Visitor refresh cooldown, seconds", 60, 86400),
    Spec("MAX_QUEUED_SCANS", int, "scanner", "Country searches allowed to wait", 0, 20,
         apply=lambda v: setattr(_scan_service(), "max_queued", int(v))),
    Spec("SCAN_RATE_LIMIT_PER_MINUTE", int, "cache", "Scan-starting requests per IP per minute", 1, 600,
         apply=_limiter("app.api.rate_limit", "scan_limiter")),
    Spec("AUTO_REFRESH", bool, "scanner", "Rescan when the main cache expires", restart=True),
    # Probes
    Spec("PROBE_TARGET_COUNT", int, "probes", "Configs of each scan that probes check", 1, 100),
    Spec("PROBE_HEARTBEAT_SECONDS", int, "probes", "Heartbeat interval told to probes, seconds", 10, 600),
    Spec("PROBE_OFFLINE_AFTER_SECONDS", int, "probes", "Offline after silence of, seconds", 30, 3600),
    Spec("PROBE_RESULT_TTL_SECONDS", int, "probes", "Reachability result lifetime, seconds", 600, 7 * 86400),
    Spec("PROBE_JOB_TTL_SECONDS", int, "probes", "Unclaimed job lifetime, seconds", 60, 86400),
    Spec("PROBE_JOB_MAX_ATTEMPTS", int, "probes", "Lease attempts per job", 1, 10),
    Spec("PROBE_JOBS_PER_POLL", int, "probes", "Jobs handed out per poll", 1, 50),
    Spec("PROBE_REQUESTS_PER_MINUTE", int, "probes", "Agent requests per probe per minute", 10, 1000,
         apply=_limiter("app.probes.api", "probe_limiter")),
    Spec("PROBE_ENROLLMENTS_PER_HOUR", int, "probes", "Provider sign-ups per IP per hour", 1, 1000,
         apply=_limiter("app.probes.api", "enroll_limiter")),
    Spec("PROBE_AUTO_APPROVE", bool, "verification", "Skip verification for new probes (closed deployments only)"),
    Spec("PROBE_VERIFY_TIMEOUT_SECONDS", int, "verification", "Time a probe has to pass verification, seconds", 60, 86400),
    Spec("PROBE_VERIFY_RETRY_SECONDS", int, "verification", "Wait before retrying a failed verification, seconds", 60, 7 * 86400),
    Spec("PROBE_VERIFY_MAX_ATTEMPTS", int, "verification", "Automatic verification attempts", 1, 20),
    Spec("PROBE_SPOT_CHECK_SECONDS", int, "verification", "Spot check interval per probe, seconds (0 = off)", 0, 7 * 86400),
    # Ranking
    Spec("RANKING_USE_PROBE_SCORE", bool, "ranking", "Rank probe-confirmed configs first"),
)}

# The environment's values, before any override: "reset" goes back to these.
DEFAULTS = {key: getattr(config, key) for key in SPECS}


def coerce(key: str, value: Any) -> Any:
    spec = SPECS.get(key)
    if spec is None:
        raise KeyError(key)
    if spec.type is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false", "1", "0"):
            return value.lower() in ("true", "1")
        raise ValueError(f"{key} must be true or false")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{key} must be a number")
    try:
        number = spec.type(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number") from None
    if spec.type is int and float(value) != number:
        raise ValueError(f"{key} must be a whole number")
    if (spec.min is not None and number < spec.min) or (spec.max is not None and number > spec.max):
        raise ValueError(f"{key} must be between {spec.min:g} and {spec.max:g}")
    return number


def _check_combination(values: dict) -> None:
    if values["DEEP_MIN_SUCCESS"] > values["DEEP_ATTEMPTS"]:
        raise ValueError("DEEP_MIN_SUCCESS cannot exceed DEEP_ATTEMPTS")
    if values["PROBE_OFFLINE_AFTER_SECONDS"] < 2 * values["PROBE_HEARTBEAT_SECONDS"]:
        raise ValueError("PROBE_OFFLINE_AFTER_SECONDS must be at least two heartbeats")


def _apply(key: str, value: Any) -> None:
    setattr(config, key, value)
    spec = SPECS[key]
    if spec.apply and not spec.restart:
        spec.apply(value)


def stored() -> dict:
    return {r["key"]: json.loads(r["value"]) for r in db.store().all("SELECT key, value FROM settings")}


def load() -> int:
    """At startup: apply every stored override that is still valid."""
    applied = 0
    for key, value in stored().items():
        try:
            _apply(key, coerce(key, value))
            applied += 1
        except (KeyError, ValueError):
            events.record("admin", "setting_ignored", f"Stored setting {key} is invalid and was ignored",
                          severity="warning")
    return applied


def view() -> list[dict]:
    overrides = stored()
    return [{"key": s.key, "type": s.type.__name__, "category": s.category, "label": s.label, "min": s.min,
             "max": s.max, "value": getattr(config, s.key), "default": DEFAULTS[s.key],
             "overridden": s.key in overrides, "restart_required": s.restart} for s in SPECS.values()]


def update(changes: dict) -> list[dict]:
    """Validate every change first, then apply them all: never half an update."""
    coerced = {key: coerce(key, value) for key, value in changes.items()}
    _check_combination({**{k: getattr(config, k) for k in SPECS}, **coerced})
    now = time.time()
    with db.store().transaction() as tx:
        for key, value in coerced.items():
            tx.execute("INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key)"
                       " DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, json.dumps(value), now))
    for key, value in coerced.items():
        _apply(key, value)
    if coerced:
        events.record("admin", "settings_changed", "Settings changed: " + ", ".join(f"{k}={v}" for k, v in coerced.items()))
    return view()


def reset(key: str) -> list[dict]:
    if key not in SPECS:
        raise KeyError(key)
    _check_combination({**{k: getattr(config, k) for k in SPECS}, key: DEFAULTS[key]})
    db.store().run("DELETE FROM settings WHERE key=?", (key,))
    _apply(key, DEFAULTS[key])
    events.record("admin", "settings_changed", f"Setting {key} reset to its default ({DEFAULTS[key]})")
    return view()
