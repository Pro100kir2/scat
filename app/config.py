import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

CONFIG_FILE = os.getenv("CONFIG_FILE", str(BASE_DIR / "config.txt"))
XRAY_BINARY = os.getenv("XRAY_BINARY", str(BASE_DIR / "xray"))
TOP_COUNT = int(os.getenv("TOP_COUNT", "5"))
WORKERS = int(os.getenv("WORKERS", "50"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))
TIMEOUT = float(os.getenv("TIMEOUT", "10.0"))
FAST_TIMEOUT = float(os.getenv("FAST_TIMEOUT", "3.0"))  # For first pass
RETRIES = int(os.getenv("RETRIES", "1"))
FAST_RETRIES = int(os.getenv("FAST_RETRIES", "1"))  # Add one retry for first pass
RECHECK_COUNT = int(os.getenv("RECHECK_COUNT", "25"))
RECHECK_ATTEMPTS = int(os.getenv("RECHECK_ATTEMPTS", "3"))
XRAY_WORKERS = int(os.getenv("XRAY_WORKERS", "3"))  # Parallel Xray processes

TEST_URL = os.getenv("TEST_URL", "https://www.cloudflare.com/cdn-cgi/trace")
MAX_NODES = int(os.getenv("MAX_NODES", "5000"))
SOURCE_CONCURRENCY = int(os.getenv("SOURCE_CONCURRENCY", "20"))
PORT_START = int(os.getenv("PORT_START", "20000"))

TOP_FILE = str(BASE_DIR / "top5.txt")
RESULTS_FILE = str(BASE_DIR / "results.json")

AUTO_SCAN_ON_STARTUP = os.getenv("AUTO_SCAN_ON_STARTUP", "true").lower() in ("true", "1", "yes")
AUTO_SCAN_INTERVAL_SECONDS = int(os.getenv("AUTO_SCAN_INTERVAL_SECONDS", "1800"))
