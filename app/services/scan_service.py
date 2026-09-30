"""24-hour result caches with single-flight scanning.

Each query (all countries, or one exit country) has its own cache file. Visitors read
the cache; a scan runs only when it is missing, expired or written by another scanner
version, and at most one scan per query is ever in flight: concurrent requests join
the running task. Scans for different queries queue up behind each other because the
scanner itself runs one at a time; other processes sharing DATA_DIR wait on a file lock.
"""
import asyncio
import fcntl
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Literal, Optional

from app import config
from app.services.scanner import ScanFailed, ScanQuery, VLESSScannerManager, scanner_manager

logger = logging.getLogger("skat.cache")

CACHE_SCHEMA = 2
# A scan that found nothing is usually temporary (or a rare country): retry sooner.
EMPTY_RESULT_TTL_SECONDS = 60 * 60
# After a failed scan, requests don't start a new one until this cooldown passes.
FAILURE_COOLDOWN_SECONDS = 5 * 60
LOCK_POLL_SECONDS = 2.0

ALL = ScanQuery()
RequestOutcome = Literal["cached", "started", "joined", "cooldown", "busy"]
ScanListener = Callable[[ScanQuery, dict], Awaitable[None]]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ScanCache:
    """JSON file cache. Writes are atomic so readers never see a half-written file."""

    def __init__(self, path: Path, ttl_seconds: int, scanner_version: str):
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.scanner_version = scanner_version

    def load(self) -> Optional[dict]:
        try:
            entry = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable cache %s: %s", self.path, exc)
            return None
        if entry.get("schema") != CACHE_SCHEMA or entry.get("scanner_version") != self.scanner_version:
            logger.info("Ignoring %s from scanner version %r", self.path.name, entry.get("scanner_version"))
            return None
        return entry

    def save(self, result: dict, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        ttl = self.ttl_seconds if result["top"] else min(self.ttl_seconds, EMPTY_RESULT_TTL_SECONDS)
        entry = {
            "schema": CACHE_SCHEMA,
            "scanner_version": self.scanner_version,
            "created_at": _iso(now),
            "created_ts": now,
            "expires_at": _iso(now + ttl),
            "expires_ts": now + ttl,
            "count": len(result["top"]),
            **result,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
        return entry

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def mtime(self) -> Optional[float]:
        try:
            return self.path.stat().st_mtime
        except FileNotFoundError:
            return None


class _Slot:
    """Cache, running task and last failure for one query."""

    def __init__(self, cache: ScanCache):
        self.cache = cache
        self.entry: Optional[dict] = None
        self.mtime: Optional[float] = None
        self.task: Optional[asyncio.Task] = None
        self.error: Optional[str] = None
        self.error_ts: Optional[float] = None

    def current(self) -> Optional[dict]:
        """Cache entry, re-read when another process has replaced the file."""
        mtime = self.cache.mtime()
        if mtime != self.mtime:
            self.entry = self.cache.load()
            self.mtime = mtime
        return self.entry

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()


class ScanService:
    def __init__(
        self,
        scanner: VLESSScannerManager,
        data_dir: Path,
        ttl_seconds: int,
        scanner_version: str,
        lock_path: Path,
        max_queued: int = config.MAX_QUEUED_SCANS,
    ):
        self.scanner = scanner
        self.data_dir = data_dir
        self.ttl_seconds = ttl_seconds
        self.scanner_version = scanner_version
        self.lock_path = lock_path
        self.max_queued = max_queued
        self._slots: dict[str, _Slot] = {}
        self._refreshed_ts: Optional[float] = None
        self.stats = {"cache_hits": 0, "scans_started": 0, "requests_joined": 0, "rejected_busy": 0}
        self._listeners: list[ScanListener] = []
        self._listener_tasks: set[asyncio.Task] = set()

    def add_listener(self, listener: ScanListener) -> None:
        """Called with (query, cache entry) after every successful scan, outside the scan lock."""
        self._listeners.append(listener)

    def _notify(self, query: ScanQuery, entry: dict) -> None:
        for listener in self._listeners:
            task = asyncio.create_task(self._call_listener(listener, query, entry), name=f"skat-scan-listener-{query.key}")
            self._listener_tasks.add(task)
            task.add_done_callback(self._listener_tasks.discard)

    @staticmethod
    async def _call_listener(listener: ScanListener, query: ScanQuery, entry: dict) -> None:
        try:
            await listener(query, entry)
        except Exception:
            logger.exception("[%s] scan listener failed", query.key)

    def _slot(self, query: ScanQuery) -> _Slot:
        if query.key not in self._slots:
            path = self.data_dir / f"scan_{query.key}.json"
            self._slots[query.key] = _Slot(ScanCache(path, self.ttl_seconds, self.scanner_version))
        return self._slots[query.key]

    # --- cache state -------------------------------------------------------
    def entry(self, query: ScanQuery = ALL) -> Optional[dict]:
        return self._slot(query).current()

    def is_fresh(self, query: ScanQuery = ALL, now: Optional[float] = None) -> bool:
        entry = self.entry(query)
        return bool(entry) and (time.time() if now is None else now) < entry["expires_ts"]

    def cache_state(self, query: ScanQuery = ALL) -> Literal["fresh", "stale", "empty"]:
        if self.entry(query) is None:
            return "empty"
        return "fresh" if self.is_fresh(query) else "stale"

    def is_scanning(self, query: ScanQuery = ALL) -> bool:
        return self._slot(query).running

    def cache_meta(self, query: ScanQuery = ALL) -> Optional[dict]:
        entry = self.entry(query)
        if entry is None:
            return None
        keys = ("created_at", "expires_at", "scanner_version", "count", "total_nodes", "tested", "working",
                "duration_seconds", "country")
        return {key: entry.get(key) for key in keys}

    # --- scanning ----------------------------------------------------------
    def _cooling_down(self, slot: _Slot) -> bool:
        return slot.error_ts is not None and time.time() - slot.error_ts < FAILURE_COOLDOWN_SECONDS

    def would_start(self, query: ScanQuery = ALL) -> bool:
        """True if request_scan(query) would launch a new scan (used for rate limiting)."""
        slot = self._slot(query)
        return not slot.running and not self.is_fresh(query) and not self._cooling_down(slot)

    def request_scan(self, query: ScanQuery = ALL, force: bool = False) -> RequestOutcome:
        """Start a scan unless a valid cache exists. Never starts two for the same query."""
        slot = self._slot(query)
        if slot.running:
            self.stats["requests_joined"] += 1
            return "joined"
        if not force and self.is_fresh(query):
            self.stats["cache_hits"] += 1
            return "cached"
        if not force and self._cooling_down(slot):
            return "cooldown"
        if query.country and not force:
            waiting = sum(1 for key, s in self._slots.items() if key != ALL.key and s.running)
            if waiting >= self.max_queued:
                self.stats["rejected_busy"] += 1
                return "busy"
        self.stats["scans_started"] += 1
        slot.task = asyncio.create_task(self._run(query, force), name=f"skat-scan-{query.key}")
        return "started"

    def refresh_retry_after(self, cooldown_seconds: float, now: Optional[float] = None) -> int:
        """Seconds until visitors may force another refresh; 0 when allowed."""
        if self._refreshed_ts is None:
            return 0
        now = time.time() if now is None else now
        return max(0, math.ceil(self._refreshed_ts + cooldown_seconds - now))

    def refresh(self) -> RequestOutcome:
        """Visitor-triggered rescan that ignores the cache. The old results stay visible until it finishes."""
        outcome = self.request_scan(ALL, force=True)
        if outcome == "started":
            self._refreshed_ts = time.time()
        return outcome

    async def wait(self, query: ScanQuery = ALL) -> None:
        """Wait for the query's scan without cancelling it when the caller is cancelled."""
        task = self._slot(query).task
        if task is not None:
            await asyncio.shield(task)

    async def _run(self, query: ScanQuery, force: bool) -> None:
        slot = self._slot(query)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "w") as lock_file:
            waited = False
            while True:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    waited = True  # another scan (this process or another) is running
                    await asyncio.sleep(LOCK_POLL_SECONDS)
            try:
                if waited and not force and self.is_fresh(query):
                    return  # someone refreshed this cache while we waited
                logger.info("[%s] starting scan (cache: %s)", query.key, self.cache_state(query))
                result = await self.scanner.run_scan(query)
                entry = slot.cache.save(result)
                slot.error = slot.error_ts = None
                self._notify(query, entry)
            except ScanFailed as exc:
                slot.error, slot.error_ts = str(exc), time.time()
                logger.error("[%s] scan failed: %s", query.key, exc)
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    async def refresh_forever(self, scan_now: bool = True) -> None:
        """Keep the main (all countries) cache warm: rescan whenever it expires."""
        while True:
            if scan_now:
                self.request_scan(ALL)
            scan_now = True
            await self.wait(ALL)
            entry = self.entry(ALL)
            if entry and self.is_fresh(ALL):
                delay = entry["expires_ts"] - time.time()
            else:
                delay = FAILURE_COOLDOWN_SECONDS
            await asyncio.sleep(max(delay, 60))

    def clear(self, query: Optional[ScanQuery] = None) -> None:
        paths = [self._slot(query).cache.path] if query else list(self.data_dir.glob("scan_*.json"))
        for path in paths:
            path.unlink(missing_ok=True)
        for slot in self._slots.values():
            slot.entry = slot.mtime = None

    def last_error(self, query: ScanQuery = ALL) -> Optional[str]:
        return self._slot(query).error

    def status(self, query: ScanQuery = ALL) -> dict:
        slot = self._slot(query)
        live = self.scanner.get_status()
        active = slot.running and live.get("query") == query.key
        return {
            "cache_state": self.cache_state(query),
            "is_scanning": slot.running,
            # Waiting for another query's scan to finish first.
            "queued": slot.running and live.get("query") not in (None, query.key),
            "progress": live if active else None,
            # Details stay in the server log; clients only learn that the last attempt failed.
            "last_scan_failed": slot.error is not None,
            "cache": self.cache_meta(query),
        }


scan_service = ScanService(
    scanner_manager,
    config.DATA_DIR,
    config.CACHE_TTL_SECONDS,
    config.SCANNER_VERSION,
    config.LOCK_FILE,
)
