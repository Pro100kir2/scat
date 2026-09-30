"""SQLite persistence for providers, probes, jobs and results.

The scan cache stays JSON files; probe state needs transactional, concurrent-safe
writes from many agents, which is what SQLite (stdlib, one file in DATA_DIR) gives us.
All calls happen on the event loop thread and are short, so one connection is enough.
"""
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    name_key    TEXT NOT NULL,
    country     TEXT NOT NULL,
    region      TEXT,
    asn         INTEGER,
    info        TEXT,
    created_at  REAL NOT NULL,
    UNIQUE (name_key, country)
);

CREATE TABLE IF NOT EXISTS enrollments (
    id            TEXT PRIMARY KEY,
    provider_id   TEXT NOT NULL REFERENCES providers(id),
    token_hash    TEXT NOT NULL UNIQUE,
    region        TEXT,
    asn           INTEGER,
    info          TEXT,
    client_ip     TEXT,
    created_at    REAL NOT NULL,
    expires_at    REAL NOT NULL,
    used_at       REAL,
    probe_id      TEXT,
    diag_verdict  TEXT,
    diag_profile  TEXT,
    diag_json     TEXT,
    diag_at       REAL
);

CREATE TABLE IF NOT EXISTS probes (
    id                   TEXT PRIMARY KEY,
    provider_id          TEXT NOT NULL REFERENCES providers(id),
    enrollment_id        TEXT,
    status               TEXT NOT NULL,          -- pending | approved | disabled
    secret_hash          TEXT NOT NULL,
    prev_secret_hash     TEXT,
    prev_secret_expires  REAL,
    credential_at        REAL NOT NULL,
    region               TEXT,
    asn                  INTEGER,
    platform             TEXT,
    profile              TEXT,
    agent_version        TEXT,
    public_ip            TEXT,
    created_at           REAL NOT NULL,
    last_seen            REAL,
    last_heartbeat       REAL,
    control_ok           INTEGER,
    control_at           REAL
);
CREATE INDEX IF NOT EXISTS probes_provider ON probes(provider_id);

CREATE TABLE IF NOT EXISTS jobs (
    id             TEXT PRIMARY KEY,
    probe_id       TEXT NOT NULL REFERENCES probes(id),
    key            TEXT NOT NULL,
    type           TEXT NOT NULL,
    payload        TEXT NOT NULL,
    is_control     INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL,                -- queued | leased | done | expired
    attempts       INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    leased_at      REAL,
    lease_expires  REAL,
    expires_at     REAL NOT NULL,
    completed_at   REAL
);
-- At most one open job per probe and target: the queue deduplicates itself.
CREATE UNIQUE INDEX IF NOT EXISTS jobs_open ON jobs(probe_id, key) WHERE status IN ('queued', 'leased');
CREATE INDEX IF NOT EXISTS jobs_probe_status ON jobs(probe_id, status);

CREATE TABLE IF NOT EXISTS results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT,
    probe_id      TEXT NOT NULL,
    provider_id   TEXT NOT NULL,
    key           TEXT NOT NULL,
    type          TEXT NOT NULL,
    ip            TEXT NOT NULL,
    port          INTEGER NOT NULL,
    server_name   TEXT,
    outcome       TEXT NOT NULL,                 -- SUCCESS | FAILED | TIMEOUT | ERROR
    error         TEXT,
    latency_ms    REAL,
    inconclusive  INTEGER NOT NULL DEFAULT 0,
    checked_at    REAL NOT NULL,
    expires_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS results_key ON results(key, expires_at);
CREATE INDEX IF NOT EXISTS results_probe_key ON results(probe_id, key, expires_at);

-- The configs probes currently check (the best of the latest full scan), with their targets.
CREATE TABLE IF NOT EXISTS targets (
    rank             INTEGER PRIMARY KEY,
    fingerprint      TEXT NOT NULL,
    key              TEXT,
    payload          TEXT,
    reject_reason    TEXT,
    scan_created_at  TEXT
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            with self._lock:
                if self._conn is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute("PRAGMA busy_timeout=10000")
                    version = conn.execute("PRAGMA user_version").fetchone()[0]
                    if version < SCHEMA_VERSION:
                        conn.executescript(SCHEMA)
                        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                    self._conn = conn
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # --- small helpers ------------------------------------------------------
    def one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def run(self, sql: str, params: Iterable[Any] = ()) -> int:
        return self.conn.execute(sql, tuple(params)).rowcount

    def transaction(self):
        return _Transaction(self.conn)

    @staticmethod
    def dumps(value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def loads(value: str) -> Any:
        return json.loads(value)


class _Transaction:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
