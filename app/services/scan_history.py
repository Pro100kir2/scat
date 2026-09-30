"""Every scan's outcome, kept in SQLite for the admin panel (the JSON cache keeps only the latest)."""
import logging
import time
from typing import Optional

from app import db, events

logger = logging.getLogger("skat.scans")

KEEP = 2000  # rows


class ScanHistory:
    def started(self, query_key: str, trigger: str) -> Optional[int]:
        try:
            cursor = db.store().conn.execute(
                "INSERT INTO scans (query, trigger, status, started_at) VALUES (?, ?, 'running', ?)",
                (query_key, trigger, time.time()))
            return cursor.lastrowid
        except Exception:
            logger.exception("scan history unavailable")
            return None

    def finished(self, scan_id: Optional[int], *, result: Optional[dict] = None, error: Optional[str] = None,
                 cancelled: bool = False) -> None:
        if scan_id is None:
            return
        now = time.time()
        try:
            store = db.store()
            row = store.one("SELECT * FROM scans WHERE id=?", (scan_id,))
            if row is None:
                return
            duration = round(now - row["started_at"], 2)
            if result is not None:
                sources = result.get("sources") or []
                failed_sources = [s for s in sources if not s.get("success")]
                tested, working = result.get("tested") or 0, result.get("working") or 0
                detail = {
                    "sources": sources,
                    "top": [{"fingerprint": n.get("fingerprint"), "address": n.get("address"), "port": n.get("port"),
                             "latency_ms": n.get("latency_ms"), "speed_mbps": n.get("speed_mbps"),
                             "exit_country": n.get("exit_country")} for n in result.get("top") or []],
                }
                store.run(
                    "UPDATE scans SET status='completed', finished_at=?, duration_seconds=?, source_count=?, sources_ok=?,"
                    " candidate_count=?, unique_count=?, tested_count=?, passed_count=?, failed_count=?, error_count=?,"
                    " top_count=?, detail=? WHERE id=?",
                    (now, duration, len(sources), len(sources) - len(failed_sources),
                     sum(s.get("vless_count") or 0 for s in sources), result.get("total_nodes"), tested, working,
                     max(tested - working, 0), len(failed_sources), len(result.get("top") or []),
                     store.dumps(detail), scan_id))
                events.record("scanner", "scan_completed",
                              f"Scan #{scan_id} ({row['query']}) finished in {duration:.0f}s: {working} working of "
                              f"{tested} tested, {len(result.get('top') or [])} in the top", scan_id=scan_id)
            else:
                status = "cancelled" if cancelled else "failed"
                store.run("UPDATE scans SET status=?, finished_at=?, duration_seconds=?, error=? WHERE id=?",
                          (status, now, duration, (error or "")[:500] or None, scan_id))
                events.record("scanner", f"scan_{status}", f"Scan #{scan_id} ({row['query']}) {status}"
                              + (f": {error}" if error else ""), severity="warning" if cancelled else "error",
                              scan_id=scan_id)
            cutoff = store.one("SELECT id FROM scans ORDER BY id DESC LIMIT 1 OFFSET ?", (KEEP,))
            if cutoff:
                store.run("DELETE FROM scans WHERE id <= ?", (cutoff["id"],))
        except Exception:
            logger.exception("scan history unavailable")

    def probe_round(self, scan_id: Optional[int], summary: dict) -> None:
        if scan_id is None:
            return
        try:
            db.store().run("UPDATE scans SET probe_count=?, probe_jobs=? WHERE id=?",
                           (summary.get("probes"), summary.get("jobs_created"), scan_id))
        except Exception:
            logger.exception("scan history unavailable")

    def interrupted(self) -> int:
        """Scans still "running" in the database when the process starts were cut off by a restart."""
        try:
            return db.store().run("UPDATE scans SET status='failed', error='interrupted by a restart', finished_at=?"
                                  " WHERE status='running'", (time.time(),))
        except Exception:
            logger.exception("scan history unavailable")
            return 0

    def list(self, *, status: Optional[str] = None, query: Optional[str] = None, page: int = 1,
             per_page: int = 25) -> dict:
        where, params = [], []
        if status:
            where.append("status=?")
            params.append(status)
        if query:
            where.append("query=?")
            params.append(query)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        store = db.store()
        total = store.one(f"SELECT COUNT(*) AS n FROM scans {clause}", params)["n"]
        rows = store.all(f"SELECT id, query, trigger, status, started_at, finished_at, duration_seconds, source_count,"
                         f" sources_ok, candidate_count, unique_count, tested_count, passed_count, failed_count,"
                         f" error_count, top_count, probe_count, probe_jobs, error FROM scans {clause}"
                         f" ORDER BY id DESC LIMIT ? OFFSET ?", [*params, per_page, (page - 1) * per_page])
        return {"items": rows, "total": total, "page": page, "per_page": per_page}

    def get(self, scan_id: int) -> Optional[dict]:
        row = db.store().one("SELECT * FROM scans WHERE id=?", (scan_id,))
        if row:
            row["detail"] = db.store().loads(row["detail"]) if row["detail"] else None
        return row

    def last(self, status: Optional[str] = None) -> Optional[dict]:
        clause = "WHERE status=?" if status else ""
        return db.store().one(f"SELECT * FROM scans {clause} ORDER BY id DESC LIMIT 1", (status,) if status else ())


scan_history = ScanHistory()
