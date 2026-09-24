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
SCANNER_VERSION = "3.0"

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

# Requests that may start a scan, per client IP per minute.
SCAN_RATE_LIMIT_PER_MINUTE = int(os.getenv("SCAN_RATE_LIMIT_PER_MINUTE", "6"))
# Scans run one at a time; at most this many country searches may wait in line.
MAX_QUEUED_SCANS = int(os.getenv("MAX_QUEUED_SCANS", "3"))
# Trust X-Forwarded-For (only enable behind your own reverse proxy).
TRUST_PROXY_HEADERS = _env_bool("TRUST_PROXY_HEADERS", False)
