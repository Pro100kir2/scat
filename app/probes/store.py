"""SQLite persistence: providers, probes, jobs and results, plus the admin state
(sources, scan history, events, runtime settings, admin sessions).

The scan cache stays JSON files; probe state needs transactional, concurrent-safe
writes from many agents, which is what SQLite (stdlib, one file in DATA_DIR) gives us.
All calls happen on the event loop thread and are short, so one connection is enough.

The schema evolves through numbered migrations (PRAGMA user_version). Each migration runs
in one transaction, so a crash mid-way leaves the previous version intact; existing rows
are never rewritten except where a migration says so explicitly.
"""
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA_V1 = """
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

# Version 2: automatic probe verification, contact phone, per-probe counters, admin state.
MIGRATION_V2 = """
ALTER TABLE enrollments ADD COLUMN contact_phone TEXT;

ALTER TABLE probes ADD COLUMN contact_phone TEXT;
ALTER TABLE probes ADD COLUMN verify_state TEXT NOT NULL DEFAULT 'none';  -- none | running | passed | failed | skipped
ALTER TABLE probes ADD COLUMN verify_reason TEXT;
ALTER TABLE probes ADD COLUMN verify_at REAL;
ALTER TABLE probes ADD COLUMN verify_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE probes ADD COLUMN spot_at REAL;
ALTER TABLE probes ADD COLUMN jobs_done INTEGER NOT NULL DEFAULT 0;
ALTER TABLE probes ADD COLUMN jobs_success INTEGER NOT NULL DEFAULT 0;
ALTER TABLE probes ADD COLUMN jobs_failed INTEGER NOT NULL DEFAULT 0;   -- FAILED or TIMEOUT
ALTER TABLE probes ADD COLUMN jobs_error INTEGER NOT NULL DEFAULT 0;    -- ERROR
ALTER TABLE probes ADD COLUMN jobs_expired INTEGER NOT NULL DEFAULT 0;  -- leased or queued, never answered
ALTER TABLE probes ADD COLUMN last_job_at REAL;
ALTER TABLE probes ADD COLUMN last_success_at REAL;
ALTER TABLE probes ADD COLUMN last_error TEXT;
ALTER TABLE probes ADD COLUMN last_error_at REAL;
ALTER TABLE probes ADD COLUMN admin_seen_at REAL;
-- Probes that existed before automatic verification are not "new" to the admin, and the
-- ones an admin already approved count as verified.
UPDATE probes SET admin_seen_at = created_at;
UPDATE probes SET verify_state = 'passed', verify_at = created_at WHERE status = 'approved';
CREATE INDEX IF NOT EXISTS probes_status ON probes(status);
CREATE INDEX IF NOT EXISTS probes_phone ON probes(contact_phone);

ALTER TABLE jobs ADD COLUMN purpose TEXT NOT NULL DEFAULT 'scan';  -- scan | control | verify | spot | test
ALTER TABLE jobs ADD COLUMN verification_id INTEGER;
ALTER TABLE jobs ADD COLUMN nonce TEXT;
ALTER TABLE jobs ADD COLUMN nonce_hit_at REAL;
ALTER TABLE jobs ADD COLUMN nonce_hit_ip TEXT;
ALTER TABLE jobs ADD COLUMN outcome TEXT;
ALTER TABLE jobs ADD COLUMN error TEXT;
ALTER TABLE jobs ADD COLUMN http_status INTEGER;
ALTER TABLE jobs ADD COLUMN latency_ms REAL;
UPDATE jobs SET purpose = 'control' WHERE is_control = 1;
CREATE UNIQUE INDEX IF NOT EXISTS jobs_nonce ON jobs(nonce) WHERE nonce IS NOT NULL;
CREATE INDEX IF NOT EXISTS jobs_verification ON jobs(verification_id) WHERE verification_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS verifications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    probe_id     TEXT NOT NULL,
    trigger      TEXT NOT NULL,                  -- first_contact | auto_retry | admin | spot_check
    state        TEXT NOT NULL,                  -- running | passed | failed | superseded
    reason       TEXT,
    detail       TEXT,                           -- JSON: per-check outcome
    started_at   REAL NOT NULL,
    deadline     REAL NOT NULL,
    finished_at  REAL
);
CREATE INDEX IF NOT EXISTS verifications_probe ON verifications(probe_id, started_at);
CREATE INDEX IF NOT EXISTS verifications_running ON verifications(state) WHERE state = 'running';

CREATE TABLE IF NOT EXISTS sources (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    url              TEXT NOT NULL UNIQUE,
    kind             TEXT NOT NULL,                -- subscription | vless
    enabled          INTEGER NOT NULL DEFAULT 1,
    priority         INTEGER NOT NULL DEFAULT 100, -- lower first
    refresh_seconds  INTEGER NOT NULL DEFAULT 0,   -- 0: fetched on every scan
    timeout_seconds  REAL,                         -- NULL: SOURCE_TIMEOUT
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    last_fetch_at    REAL,
    last_success_at  REAL,
    last_error       TEXT,
    last_status      TEXT NOT NULL DEFAULT 'never', -- never | ok | error
    fetched_count    INTEGER,
    unique_count     INTEGER,
    valid_count      INTEGER,
    last_result      TEXT                           -- JSON
);

CREATE TABLE IF NOT EXISTS scans (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    query            TEXT NOT NULL,                -- all | ISO country
    trigger          TEXT NOT NULL,                -- auto | visitor | admin
    status           TEXT NOT NULL,                -- running | completed | failed | cancelled
    started_at       REAL NOT NULL,
    finished_at      REAL,
    duration_seconds REAL,
    source_count     INTEGER,
    sources_ok       INTEGER,
    candidate_count  INTEGER,                      -- links extracted from all sources
    unique_count     INTEGER,                      -- after deduplication
    tested_count     INTEGER,
    passed_count     INTEGER,
    failed_count     INTEGER,
    error_count      INTEGER,
    top_count        INTEGER,
    probe_count      INTEGER,                      -- probes that got jobs for this scan's targets
    probe_jobs       INTEGER,
    error            TEXT,
    detail           TEXT                          -- JSON: sources, top
);
CREATE INDEX IF NOT EXISTS scans_started ON scans(started_at);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    component  TEXT NOT NULL,                      -- probe | verification | source | scanner | cache | auth | admin | system
    severity   TEXT NOT NULL,                      -- info | warning | error
    kind       TEXT NOT NULL,
    message    TEXT NOT NULL,
    probe_id   TEXT,
    source_id  INTEGER,
    scan_id    INTEGER,
    data       TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_component ON events(component, ts);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, id);
CREATE INDEX IF NOT EXISTS events_probe ON events(probe_id) WHERE probe_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,                     -- JSON
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    id_hash     TEXT PRIMARY KEY,
    token_tag   TEXT NOT NULL,                     -- which ADMIN_TOKEN issued it; rotating the token ends sessions
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    ip          TEXT,
    user_agent  TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

MIGRATIONS = {1: SCHEMA_V1, 2: MIGRATION_V2}
SCHEMA_VERSION = max(MIGRATIONS)


def migrate(conn: sqlite3.Connection) -> int:
    """Bring the database to SCHEMA_VERSION, one migration per transaction."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for target in range(version + 1, SCHEMA_VERSION + 1):
        conn.executescript(f"BEGIN IMMEDIATE;\n{MIGRATIONS[target]}\nPRAGMA user_version={target};\nCOMMIT;")
        version = target
    return version


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
                    migrate(conn)
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
