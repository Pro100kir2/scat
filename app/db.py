"""The one SQLite database (DATA_DIR/probes.db) shared by probes and the admin state.

Resolved at call time through the probe service, so tests that swap the probe service
for an isolated one swap every other component's database with it.
"""
from app.probes.store import Store


def store() -> Store:
    from app.probes import service

    return service.probe_service.store


def get_meta(key: str) -> str | None:
    row = store().one("SELECT value FROM meta WHERE key=?", (key,))
    return row["value"] if row else None


def set_meta(key: str, value: str) -> None:
    store().run("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
