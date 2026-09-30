"""Structured application events for the admin panel: what happened, to what, how bad.

Not a copy of the server log: only events an operator acts on (a probe registered or
failed verification, a source broke, a scan failed, an admin changed something). Every
write is best effort and never raises into the caller. Values that look like credentials
or contact details are scrubbed before they are stored.
"""
import logging
import re
import time
from typing import Any, Literal, Optional

from app import db

logger = logging.getLogger("skat.events")

Component = Literal["probe", "verification", "source", "scanner", "cache", "auth", "admin", "system"]
Severity = Literal["info", "warning", "error"]
COMPONENTS = ("probe", "verification", "source", "scanner", "cache", "auth", "admin", "system")
SEVERITIES = ("info", "warning", "error")

KEEP_SECONDS = 30 * 24 * 60 * 60
KEEP_ROWS = 100_000
MAX_MESSAGE = 500

_SECRET_KEY_RE = re.compile(r"token|secret|password|authorization|cookie|phone|session|credential", re.IGNORECASE)
_SECRET_VALUE_RE = re.compile(r"(skenr_[A-Za-z0-9_-]+|Bearer\s+\S+|prb_[0-9a-f]{16}\.\S+)")


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if _SECRET_KEY_RE.search(str(k)) else scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub("[redacted]", value)[:1000]
    return value


def record(component: Component, kind: str, message: str, *, severity: Severity = "info",
           probe_id: Optional[str] = None, source_id: Optional[int] = None, scan_id: Optional[int] = None,
           data: Optional[dict] = None, ts: Optional[float] = None) -> Optional[int]:
    message = scrub(message)[:MAX_MESSAGE]
    level = {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}[severity]
    logger.log(level, "[%s/%s] %s", component, kind, message)
    try:
        store = db.store()
        cursor = store.conn.execute(
            "INSERT INTO events (ts, component, severity, kind, message, probe_id, source_id, scan_id, data)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (ts if ts is not None else time.time(), component, severity, kind, message, probe_id, source_id, scan_id,
             store.dumps(scrub(data)) if data else None),
        )
        return cursor.lastrowid
    except Exception:
        logger.exception("could not store event %s", kind)
        return None


def prune(now: Optional[float] = None) -> int:
    now = time.time() if now is None else now
    store = db.store()
    removed = store.run("DELETE FROM events WHERE ts < ?", (now - KEEP_SECONDS,))
    row = store.one("SELECT id FROM events ORDER BY id DESC LIMIT 1 OFFSET ?", (KEEP_ROWS,))
    if row:
        removed += store.run("DELETE FROM events WHERE id <= ?", (row["id"],))
    return removed


def query(*, component: Optional[str] = None, severity: Optional[str] = None, kind: Optional[str] = None,
          probe_id: Optional[str] = None, source_id: Optional[int] = None, since: Optional[float] = None,
          until: Optional[float] = None, search: Optional[str] = None, page: int = 1, per_page: int = 50) -> dict:
    where, params = [], []
    for column, value in (("component", component), ("kind", kind), ("probe_id", probe_id), ("source_id", source_id)):
        if value not in (None, ""):
            where.append(f"{column} = ?")
            params.append(value)
    if severity:
        # "warning" means warnings and worse: that is what someone filtering wants to see.
        allowed = SEVERITIES[SEVERITIES.index(severity):] if severity in SEVERITIES else (severity,)
        where.append(f"severity IN ({','.join('?' * len(allowed))})")
        params.extend(allowed)
    if since is not None:
        where.append("ts >= ?")
        params.append(since)
    if until is not None:
        where.append("ts <= ?")
        params.append(until)
    if search:
        where.append("message LIKE ? ESCAPE '\\'")
        params.append("%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    store = db.store()
    total = store.one(f"SELECT COUNT(*) AS n FROM events {clause}", params)["n"]
    rows = store.all(f"SELECT * FROM events {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                     [*params, per_page, (page - 1) * per_page])
    for row in rows:
        row["data"] = store.loads(row["data"]) if row["data"] else None
    return {"items": rows, "total": total, "page": page, "per_page": per_page}
