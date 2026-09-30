import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("true", "1", "yes")


def _env_list(name: str, default: str = "") -> list[str]:
    return [item.strip().rstrip("/") for item in os.getenv(name, default).split(",") if item.strip()]


# --- Scanner -----------------------------------------------------------------
CONFIG_FILE = os.getenv("CONFIG_FILE", str(BASE_DIR / "config.txt"))
XRAY_BINARY = os.getenv("XRAY_BINARY", str(BASE_DIR / "xray"))
MAX_NODES = int(os.getenv("MAX_NODES", "30000"))
SOURCE_CONCURRENCY = int(os.getenv("SOURCE_CONCURRENCY", "20"))
# Source lists are several MB; they need far more time than a single node check.
SOURCE_TIMEOUT = float(os.getenv("SOURCE_TIMEOUT", "45"))
# Sources are admin-configurable URLs: a response larger than this is refused, and at most
# this many redirects are followed (each one re-checked against the SSRF policy).
SOURCE_MAX_BYTES = int(os.getenv("SOURCE_MAX_BYTES", str(32 * 1024 * 1024)))
SOURCE_MAX_REDIRECTS = int(os.getenv("SOURCE_MAX_REDIRECTS", "3"))
PORT_START = int(os.getenv("PORT_START", "20000"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "500"))  # nodes per Xray process
WORKERS = int(os.getenv("WORKERS", "300"))  # concurrent qualification checks
TIMEOUT = float(os.getenv("TIMEOUT", "4.0"))  # one small request through a node, seconds

# Pipeline: all sources → first CANDIDATE_COUNT genuinely working → deep checks → TOP_COUNT best.
CANDIDATE_COUNT = int(os.getenv("CANDIDATE_COUNT", "25"))
TOP_COUNT = int(os.getenv("TOP_COUNT", "5"))

# Qualification: a small real payload catches links that stall after the first few KB.
QUALIFY_DOWNLOAD_BYTES = 256 * 1024
QUALIFY_DOWNLOAD_TIMEOUT = 10.0

# Deep checks, run only on the candidates.
DEEP_ATTEMPTS = int(os.getenv("DEEP_ATTEMPTS", "5"))
DEEP_MIN_SUCCESS = int(os.getenv("DEEP_MIN_SUCCESS", "4"))  # of DEEP_ATTEMPTS
DEEP_DOWNLOAD_BYTES = 2 * 1024 * 1024
DEEP_DOWNLOAD_TIMEOUT = 25.0
DEEP_DOWNLOAD_CONCURRENCY = 4  # downloads share our own bandwidth; keep them few

# Probe endpoints. The trace page proves the tunnel works and reports the exit IP/country.
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
TRACE_HOST = "www.cloudflare.com"
REACH_URL = "https://www.gstatic.com/generate_204"  # an unrelated second destination
REACH_STATUS = 204
SPEED_URL = "https://speed.cloudflare.com/__down"

# Bump when ranking/testing logic changes so caches written by older logic are ignored.
SCANNER_VERSION = "3.1"

# --- Cache -------------------------------------------------------------------
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
LOCK_FILE = DATA_DIR / "scan.lock"
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", str(24 * 60 * 60)))

# Scan on startup when the cache is missing or expired, and again whenever it expires.
AUTO_SCAN_ON_STARTUP = _env_bool("AUTO_SCAN_ON_STARTUP", True)
AUTO_REFRESH = _env_bool("AUTO_REFRESH", True)

# --- HTTP --------------------------------------------------------------------
# Exact origins, e.g. "https://skat.vercel.app,http://localhost:5173".
ALLOWED_ORIGINS = _env_list("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
# Optional regex for Vercel preview URLs, e.g. r"https://skat-[a-z0-9-]+\.vercel\.app".
ALLOWED_ORIGIN_REGEX = os.getenv("ALLOWED_ORIGIN_REGEX") or None

# Required for /api/admin/*; admin endpoints are disabled while it is empty.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
# Browser sessions of the admin panel (the token is exchanged once for an HttpOnly cookie).
ADMIN_SESSION_TTL_SECONDS = int(os.getenv("ADMIN_SESSION_TTL_SECONDS", str(12 * 60 * 60)))
ADMIN_SESSION_IDLE_SECONDS = int(os.getenv("ADMIN_SESSION_IDLE_SECONDS", str(2 * 60 * 60)))

# Requests that may start a scan, per client IP per minute.
SCAN_RATE_LIMIT_PER_MINUTE = int(os.getenv("SCAN_RATE_LIMIT_PER_MINUTE", "6"))
# After a visitor forces a fresh scan, nobody can force another for this long.
REFRESH_COOLDOWN_SECONDS = int(os.getenv("REFRESH_COOLDOWN_SECONDS", str(10 * 60)))
# Scans run one at a time; at most this many country searches may wait in line.
MAX_QUEUED_SCANS = int(os.getenv("MAX_QUEUED_SCANS", "3"))
# Trust X-Forwarded-For (only enable behind your own reverse proxy).
TRUST_PROXY_HEADERS = _env_bool("TRUST_PROXY_HEADERS", False)

# --- Provider probes -----------------------------------------------------------
# Probes are small agents on volunteers' devices inside ISP networks. They only test
# whether a VPN endpoint is reachable from that network (TCP/TLS/HTTP), never VLESS.
# Their confirmations re-rank the backend-verified configs (see RANKING_USE_PROBE_SCORE).
PROBE_DB_PATH = Path(os.getenv("PROBE_DB_PATH", str(DATA_DIR / "probes.db")))
# Public https origin of this API, used in install commands. Empty = taken from the request.
PUBLIC_API_URL = os.getenv("PUBLIC_API_URL", "").strip().rstrip("/")
# Where the prebuilt probe binaries and SHA256SUMS live (built into the backend image).
PROBE_DIST_DIR = Path(os.getenv("PROBE_DIST_DIR", str(BASE_DIR / "probe" / "dist")))
PROBE_INSTALLER = Path(os.getenv("PROBE_INSTALLER", str(BASE_DIR / "probe" / "install.sh")))
# New probes stay "pending" (their results are ignored) until they pass automatic verification
# (docs/PROBES.md). true skips verification entirely: only for closed deployments.
PROBE_AUTO_APPROVE = _env_bool("PROBE_AUTO_APPROVE", False)
# Verification: the probe must fetch a one-time URL on this API (the server sees the request
# arrive) and report it. Empty = PUBLIC_API_URL.
PROBE_VERIFY_URL = os.getenv("PROBE_VERIFY_URL", "").strip().rstrip("/")
PROBE_VERIFY_TIMEOUT_SECONDS = int(os.getenv("PROBE_VERIFY_TIMEOUT_SECONDS", str(10 * 60)))
PROBE_VERIFY_RETRY_SECONDS = int(os.getenv("PROBE_VERIFY_RETRY_SECONDS", str(30 * 60)))  # after a failure
PROBE_VERIFY_MAX_ATTEMPTS = int(os.getenv("PROBE_VERIFY_MAX_ATTEMPTS", "3"))  # automatic ones; admins can always re-run
# Approved probes get a hidden one-time URL among their jobs this often; answering SUCCESS
# without the request ever arriving means the probe invents results. 0 disables spot checks.
PROBE_SPOT_CHECK_SECONDS = int(os.getenv("PROBE_SPOT_CHECK_SECONDS", str(6 * 60 * 60)))

PROBE_HEARTBEAT_SECONDS = int(os.getenv("PROBE_HEARTBEAT_SECONDS", "30"))
PROBE_OFFLINE_AFTER_SECONDS = int(os.getenv("PROBE_OFFLINE_AFTER_SECONDS", "100"))  # ~3 missed heartbeats
PROBE_RESULT_TTL_SECONDS = int(os.getenv("PROBE_RESULT_TTL_SECONDS", str(24 * 60 * 60)))
PROBE_JOB_TTL_SECONDS = int(os.getenv("PROBE_JOB_TTL_SECONDS", str(15 * 60)))  # unclaimed jobs expire
PROBE_JOB_MAX_ATTEMPTS = int(os.getenv("PROBE_JOB_MAX_ATTEMPTS", "2"))
PROBE_JOBS_PER_POLL = int(os.getenv("PROBE_JOBS_PER_POLL", "10"))
PROBE_TARGET_COUNT = int(os.getenv("PROBE_TARGET_COUNT", str(CANDIDATE_COUNT)))  # best configs checked by probes
PROBE_ENROLLMENT_TTL_SECONDS = int(os.getenv("PROBE_ENROLLMENT_TTL_SECONDS", str(24 * 60 * 60)))
PROBE_CREDENTIAL_MAX_AGE_SECONDS = int(os.getenv("PROBE_CREDENTIAL_MAX_AGE_SECONDS", str(30 * 24 * 60 * 60)))
# A probe that cannot reach this itself is having network trouble; its failures don't count then.
PROBE_CONTROL_URL = os.getenv("PROBE_CONTROL_URL", "https://www.gstatic.com/generate_204")
PROBE_REQUESTS_PER_MINUTE = int(os.getenv("PROBE_REQUESTS_PER_MINUTE", "120"))  # per probe
PROBE_ENROLLMENTS_PER_HOUR = int(os.getenv("PROBE_ENROLLMENTS_PER_HOUR", "10"))  # per client IP
# The probe's real network (ASN, AS holder) is looked up from the IP it registered from.
# {ip} is replaced by the address; empty disables detection (the typed ASN is used then).
ASN_LOOKUP_URL = os.getenv("ASN_LOOKUP_URL", "https://stat.ripe.net/data/prefix-overview/data.json?resource={ip}")

# --- Ranking ---------------------------------------------------------------------
# The backend's Xray checks decide which configs work; among those, configs confirmed
# reachable by more providers' probes rank first (docs/RANKING.md). false: probe
# confirmations are shown but the order is the backend's alone.
RANKING_USE_PROBE_SCORE = _env_bool("RANKING_USE_PROBE_SCORE", True)
