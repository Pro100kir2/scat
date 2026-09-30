"""VLESS sources: where scans get their configs. Stored in SQLite, managed from the admin panel.

Migration from config.txt: the first time the table is used it is filled once from
CONFIG_FILE (the file itself is left untouched as a backup). From then on the table is the
only source of truth; deleting every source really means "no sources".

A source is either a remote list (`subscription`: plain or base64 VLESS links, fetched
through safe_fetch, never directly) or one `vless://` link. A remote list may be reused
from its last successful download for `refresh_seconds`, so a slow or rate-limited list is
not fetched on every scan; 0 fetches it every time.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlsplit

from app import config, db, events
from app.services import safe_fetch

logger = logging.getLogger("skat.sources")

Kind = Literal["subscription", "vless"]
IMPORTED_KEY = "sources_imported_from_config"
MAX_NAME = 80
MAX_REFRESH_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class SourceSpec:
    id: int
    name: str
    url: str
    kind: Kind
    timeout: float
    refresh_seconds: int
    last_success_at: Optional[float]


def _cache_dir() -> Path:
    return db.store().path.parent / "sources"


def _cache_file(source_id: int) -> Path:
    return _cache_dir() / f"{int(source_id)}.txt"


def default_name(url: str) -> str:
    if url.startswith("vless://"):
        return "VLESS link " + (urlsplit(url).hostname or "")[:40]
    parts = urlsplit(url)
    path = parts.path.rstrip("/").rsplit("/", 1)[-1]
    return f"{parts.hostname or 'source'}{'/' + path if path else ''}"[:MAX_NAME]


def kind_of(url: str) -> Kind:
    return "vless" if url.startswith("vless://") else "subscription"


def read_config_file(path: Optional[str] = None) -> list[str]:
    """The legacy list: one URL or vless:// link per line, # comments."""
    file = Path(path or config.CONFIG_FILE)
    if not file.exists():
        return []
    lines = []
    for raw in file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if " #" in line:
            line = line.split(" #", 1)[0].strip()
        if line.startswith(("http://", "https://", "vless://")):
            lines.append(line)
    return list(dict.fromkeys(lines))


def ensure_imported() -> None:
    if db.get_meta(IMPORTED_KEY):
        return
    now = time.time()
    urls = read_config_file()
    store = db.store()
    with store.transaction() as tx:
        if tx.execute("SELECT value FROM meta WHERE key=?", (IMPORTED_KEY,)).fetchone():
            return
        for priority, url in enumerate(urls):
            tx.execute("INSERT OR IGNORE INTO sources (name, url, kind, priority, created_at, updated_at)"
                       " VALUES (?,?,?,?,?,?)", (default_name(url), url, kind_of(url), 100 + priority, now, now))
        tx.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (IMPORTED_KEY, str(now)))
    if urls:
        events.record("source", "sources_imported", f"Imported {len(urls)} sources from {Path(config.CONFIG_FILE).name}")


def _row(row: dict) -> dict:
    out = dict(row)
    out["enabled"] = bool(out["enabled"])
    out["last_result"] = db.store().loads(out["last_result"]) if out.get("last_result") else None
    return out


def list_all() -> list[dict]:
    ensure_imported()
    return [_row(r) for r in db.store().all("SELECT * FROM sources ORDER BY priority, id")]


def get(source_id: int) -> Optional[dict]:
    ensure_imported()
    row = db.store().one("SELECT * FROM sources WHERE id=?", (source_id,))
    return _row(row) if row else None


def enabled_specs() -> list[SourceSpec]:
    ensure_imported()
    return [
        SourceSpec(r["id"], r["name"], r["url"], r["kind"], float(r["timeout_seconds"] or config.SOURCE_TIMEOUT),
                   int(r["refresh_seconds"] or 0), r["last_success_at"])
        for r in db.store().all("SELECT * FROM sources WHERE enabled=1 ORDER BY priority, id")
    ]


def validate_fields(data: dict, *, partial: bool = False) -> dict:
    """Shape checks shared by create and update; URL safety is checked separately (async)."""
    out = {}
    if "url" in data or not partial:
        url = str(data.get("url") or "").strip()
        if url.startswith("vless://"):
            if len(url) > 4096 or any(c in url for c in "\r\n\t "):
                raise ValueError("invalid vless:// link")
        else:
            safe_fetch.check_url(url)
        out["url"] = url
        out["kind"] = kind_of(url)
    if "name" in data:
        name = " ".join(str(data["name"] or "").split())[:MAX_NAME]
        out["name"] = name
    if "enabled" in data:
        out["enabled"] = 1 if data["enabled"] else 0
    if "priority" in data:
        priority = int(data["priority"])
        if not 0 <= priority <= 100_000:
            raise ValueError("priority must be 0..100000")
        out["priority"] = priority
    if "refresh_seconds" in data:
        refresh = int(data["refresh_seconds"] or 0)
        if not 0 <= refresh <= MAX_REFRESH_SECONDS:
            raise ValueError("refresh interval must be 0..7 days")
        out["refresh_seconds"] = refresh
    if "timeout_seconds" in data:
        timeout = data["timeout_seconds"]
        if timeout is not None:
            timeout = float(timeout)
            if not 1 <= timeout <= 300:
                raise ValueError("timeout must be 1..300 seconds")
        out["timeout_seconds"] = timeout
    return out


async def check_reachable_safely(url: str) -> None:
    """DNS part of the SSRF policy, for forms: raises UnsafeURL."""
    if not url.startswith("vless://"):
        await safe_fetch.validate_url(url)


async def create(data: dict) -> dict:
    ensure_imported()
    fields = validate_fields(data)
    await check_reachable_safely(fields["url"])
    now = time.time()
    fields.setdefault("name", "")
    fields["name"] = fields["name"] or default_name(fields["url"])
    store = db.store()
    if store.one("SELECT 1 FROM sources WHERE url=?", (fields["url"],)):
        raise ValueError("this URL is already a source")
    columns = ["name", "url", "kind", "enabled", "priority", "refresh_seconds", "timeout_seconds"]
    values = [fields.get("name"), fields["url"], fields["kind"], fields.get("enabled", 1), fields.get("priority", 100),
              fields.get("refresh_seconds", 0), fields.get("timeout_seconds")]
    cursor = store.conn.execute(
        f"INSERT INTO sources ({', '.join(columns)}, created_at, updated_at) VALUES ({','.join('?' * len(columns))}, ?, ?)",
        [*values, now, now])
    return get(cursor.lastrowid)


async def update(source_id: int, data: dict) -> Optional[dict]:
    if get(source_id) is None:
        return None
    fields = validate_fields(data, partial=True)
    if "url" in fields:
        await check_reachable_safely(fields["url"])
        clash = db.store().one("SELECT id FROM sources WHERE url=? AND id!=?", (fields["url"], source_id))
        if clash:
            raise ValueError("this URL is already a source")
        _cache_file(source_id).unlink(missing_ok=True)  # the cached body belonged to the old URL
    if "name" in fields and not fields["name"]:
        fields["name"] = default_name(fields.get("url") or get(source_id)["url"])
    if fields:
        assignments = ", ".join(f"{k}=?" for k in fields)
        db.store().run(f"UPDATE sources SET {assignments}, updated_at=? WHERE id=?",
                       [*fields.values(), time.time(), source_id])
    return get(source_id)


def delete(source_id: int) -> bool:
    removed = db.store().run("DELETE FROM sources WHERE id=?", (source_id,)) > 0
    _cache_file(source_id).unlink(missing_ok=True)
    return removed


async def fetch_body(spec: SourceSpec, *, use_cache: bool = True) -> tuple[str, safe_fetch.FetchResult, bool]:
    """(text, fetch result, served from cache). Falls back to nothing: a failed fetch is reported."""
    now = time.time()
    cache = _cache_file(spec.id)
    if use_cache and spec.refresh_seconds and spec.last_success_at and now - spec.last_success_at < spec.refresh_seconds:
        try:
            text = cache.read_text(encoding="utf-8")
            return text, safe_fetch.FetchResult(url=spec.url, ok=True, bytes=len(text), final_url=spec.url), True
        except OSError:
            pass
    result = await safe_fetch.fetch_text(spec.url, timeout=spec.timeout)
    if result.ok and spec.refresh_seconds:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(result.text, encoding="utf-8")
            tmp.replace(cache)
        except OSError:
            logger.warning("cannot cache source %s", spec.id)
    return result.text, result, False


def record(source_id: int, *, ok: bool, error: str = "", fetched: Optional[int] = None,
           valid: Optional[int] = None, unique: Optional[int] = None, detail: Optional[dict] = None,
           cached: bool = False) -> None:
    now = time.time()
    previous = db.store().one("SELECT last_status, name FROM sources WHERE id=?", (source_id,))
    if previous is None:
        return
    db.store().run(
        "UPDATE sources SET last_fetch_at=?, last_success_at=CASE WHEN ? THEN ? ELSE last_success_at END,"
        " last_error=?, last_status=?, fetched_count=COALESCE(?, fetched_count), valid_count=COALESCE(?, valid_count),"
        " unique_count=COALESCE(?, unique_count), last_result=? WHERE id=?",
        (now, int(ok and not cached), now, None if ok else error[:300], "ok" if ok else "error", fetched, valid, unique,
         db.store().dumps({**(detail or {}), "cached": cached, "at": now}), source_id))
    # One event per change of state, not one per scan.
    if not ok and previous["last_status"] != "error":
        events.record("source", "source_failed", f"Source “{previous['name']}” failed: {error[:200]}",
                      severity="warning", source_id=source_id)
    elif ok and previous["last_status"] == "error":
        events.record("source", "source_recovered", f"Source “{previous['name']}” works again", source_id=source_id)


async def gather_limited(coros, limit: int):
    semaphore = asyncio.Semaphore(max(1, limit))

    async def run(coro):
        async with semaphore:
            return await coro

    return await asyncio.gather(*(run(c) for c in coros))
