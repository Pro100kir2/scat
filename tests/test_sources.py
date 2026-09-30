"""VLESS sources: migration from config.txt, admin CRUD, the scanner using them, and SSRF."""
import asyncio
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import safe_fetch, sources
from app.services.scanner import VLESSScannerManager
from tests.conftest import REALITY_LINK

TOKEN = "admin-" + "s" * 40
H = {"Authorization": f"Bearer {TOKEN}"}
LINKS = "\n".join(REALITY_LINK.replace("example.com", f"n{i}.example.com") for i in range(3))


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(api_service, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    return TestClient(app)


@pytest.fixture
def public_dns(monkeypatch):
    """Every name resolves to one public address (no real DNS in tests)."""
    real = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        if host in ("private.example", "rebind.example"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", port or 0))]
        if host == "mixed.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 0)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 0))]
        try:
            return real(host, port, *args, **kwargs)  # numeric forms like 0x7f.1 resolve offline
        except OSError:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        port = self.server.server_address[1]
        routes = {
            "/list": (200, {}, LINKS.encode()),
            "/to-list": (302, {"Location": f"http://127.0.0.1:{port}/list"}, b""),
            "/to-private": (302, {"Location": "http://10.0.0.1/list"}, b""),
            "/to-metadata": (302, {"Location": "http://169.254.169.254/latest/meta-data/"}, b""),
            "/to-localhost": (302, {"Location": "http://localhost:8000/api/admin/stats"}, b""),
            "/loop": (302, {"Location": "/loop"}, b""),
            "/big": (200, {}, b"x" * (2 * 1024 * 1024)),
            "/missing": (404, {}, b"no"),
        }
        status, headers, body = routes.get(self.path, (404, {}, b""))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def source_server(monkeypatch):
    """A local list server. Only its exact address is let through the SSRF policy."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(safe_fetch, "address_allowed", lambda ip: ip == "127.0.0.1" or safe_fetch.targets.is_public_ip(ip))
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


# --- SSRF: URL policy ------------------------------------------------------------------------
BLOCKED = [
    "http://127.0.0.1/", "http://127.0.0.1:8000/api/admin/stats", "http://localhost/", "http://LOCALHOST./",
    "http://10.0.0.1/", "http://172.16.5.4/", "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.1/", "http://0.0.0.0/", "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://[fd00::1]/",
    "http://[fe80::1]/", "http://224.0.0.1/", "http://backend:8000/", "http://metadata.google.internal/",
    "http://router.lan/", "http://printer.local/", "http://x.home.arpa/",
    "ftp://example.com/list", "file:///etc/passwd", "gopher://example.com/", "javascript:alert(1)", "dict://x.y/",
    "http://user:pass@example.com/", "http://example.com@127.0.0.1/", "http://exa mple.com/", "http://example.com\r\n/",
    "", "http://" + "a" * 2050 + ".com/", "http://example.com:0/",
]


@pytest.mark.parametrize("url", BLOCKED)
def test_unsafe_urls_are_refused_before_any_dns(url):
    with pytest.raises(safe_fetch.UnsafeURL):
        safe_fetch.check_url(url)


@pytest.mark.parametrize("url", ["http://0x7f.1/", "http://127.1/", "http://rebind.example/",
                                 "http://private.example/", "http://mixed.example/"])
def test_names_resolving_to_private_addresses_are_refused(url, public_dns):
    with pytest.raises(safe_fetch.UnsafeURL):
        run(safe_fetch.validate_url(url))


def test_public_urls_pass(public_dns):
    assert run(safe_fetch.validate_url("https://raw.githubusercontent.com/x/y/main/list.txt"))


def test_pinned_resolver_answers_only_validated_hosts():
    resolver = safe_fetch._PinnedResolver()
    resolver.pinned["lists.example"] = ["93.184.216.34"]
    assert run(resolver.resolve("lists.example", 443))[0]["host"] == "93.184.216.34"
    with pytest.raises(OSError):
        run(resolver.resolve("other.example", 443))


# --- SSRF: fetching -----------------------------------------------------------------------------
def test_fetch_follows_safe_redirects_only(source_server):
    ok = run(safe_fetch.fetch_text(f"{source_server}/to-list", timeout=5))
    assert ok.ok and ok.text.count("vless://") == 3 and ok.redirects == [f"{source_server}/list"]
    for path in ("/to-private", "/to-metadata", "/to-localhost"):
        result = run(safe_fetch.fetch_text(f"{source_server}{path}", timeout=5))
        assert not result.ok and result.error.startswith("blocked"), path
    loop = run(safe_fetch.fetch_text(f"{source_server}/loop", timeout=5))
    assert not loop.ok and "too many redirects" in loop.error


def test_fetch_caps_the_body_and_reports_http_errors(source_server):
    big = run(safe_fetch.fetch_text(f"{source_server}/big", timeout=5, max_bytes=1024 * 1024))
    assert not big.ok and "larger than" in big.error
    missing = run(safe_fetch.fetch_text(f"{source_server}/missing", timeout=5))
    assert (missing.ok, missing.error) == (False, "HTTP 404")


def test_fetch_never_reaches_loopback_under_the_production_policy():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        result = run(safe_fetch.fetch_text(f"http://127.0.0.1:{port}/", timeout=2))
        s.settimeout(0.2)
        with pytest.raises(OSError):
            s.accept()  # nothing ever connected
    assert not result.ok and result.error.startswith("blocked")


# --- migration and registry ---------------------------------------------------------------------
def test_config_file_is_imported_once(tmp_path, monkeypatch):
    (tmp_path / "config.txt").write_text(
        "# comment\nhttps://a.example/list.txt\nhttps://b.example/x # note\n\nvless://u@1.1.1.1:443#x\nnot a url\n"
        "https://a.example/list.txt\n")
    rows = sources.list_all()
    assert [(r["url"], r["kind"]) for r in rows] == [
        ("https://a.example/list.txt", "subscription"), ("https://b.example/x", "subscription"),
        ("vless://u@1.1.1.1:443#x", "vless")]
    sources.delete(rows[0]["id"])
    assert len(sources.list_all()) == 2  # not re-imported: the table is the source of truth now
    assert (tmp_path / "config.txt").exists()  # the file stays as a backup


def test_admin_source_crud_and_validation(client, public_dns):
    created = client.post("/api/admin/sources", headers=H, json={"url": "https://lists.example/vless.txt"})
    assert created.status_code == 201
    source = created.json()
    assert source["name"] == "lists.example/vless.txt" and source["enabled"] and source["last_status"] == "never"
    sid = source["id"]
    for bad in ("http://127.0.0.1/x", "http://private.example/x", "file:///etc/passwd", "http://169.254.169.254/"):
        reply = client.post("/api/admin/sources", headers=H, json={"url": bad})
        assert reply.status_code == 422, bad
        assert client.patch(f"/api/admin/sources/{sid}", headers=H, json={"url": bad}).status_code == 422
    assert client.post("/api/admin/sources", headers=H, json={"url": "https://lists.example/vless.txt"}).status_code == 422
    for bad_field in ({"priority": -1}, {"refresh_seconds": 10**9}, {"timeout_seconds": 0}, {"unknown": 1}):
        assert client.patch(f"/api/admin/sources/{sid}", headers=H, json=bad_field).status_code == 422
    updated = client.patch(f"/api/admin/sources/{sid}", headers=H,
                           json={"name": "Main list", "enabled": False, "priority": 5, "refresh_seconds": 3600,
                                 "timeout_seconds": 20}).json()
    assert (updated["name"], updated["enabled"], updated["priority"], updated["refresh_seconds"],
            updated["timeout_seconds"]) == ("Main list", False, 5, 3600, 20)
    assert sources.enabled_specs() == []
    assert client.delete(f"/api/admin/sources/{sid}", headers=H).json() == {"deleted": sid}
    assert client.get(f"/api/admin/sources/{sid}", headers=H).status_code == 404
    kinds = {e["kind"] for e in sources.db.store().all("SELECT kind FROM events WHERE component='admin'")}
    assert {"source_created", "source_updated", "source_deleted"} <= kinds


def test_test_and_fetch_actions(client, source_server):
    source = sources.db.store().conn.execute(
        "INSERT INTO sources (name, url, kind, created_at, updated_at) VALUES ('local', ?, 'subscription', 0, 0)",
        (f"{source_server}/list",)).lastrowid
    tested = client.post(f"/api/admin/sources/{source}/test", headers=H).json()
    assert (tested["ok"], tested["fetched"], tested["valid"], tested["unique"]) == (True, 3, 3, 3)
    assert sources.get(source)["last_status"] == "never"  # test stores nothing
    fetched = client.post(f"/api/admin/sources/{source}/fetch", headers=H).json()
    assert fetched["source"]["last_status"] == "ok" and fetched["source"]["valid_count"] == 3
    assert fetched["source"]["last_result"]["sample"]


def test_scanner_collects_from_the_registry(source_server, monkeypatch):
    store = sources.db.store()
    good = store.conn.execute("INSERT INTO sources (name, url, kind, priority, refresh_seconds, created_at, updated_at)"
                              " VALUES ('good', ?, 'subscription', 1, 3600, 0, 0)", (f"{source_server}/list",)).lastrowid
    dup = store.conn.execute("INSERT INTO sources (name, url, kind, priority, created_at, updated_at)"
                             " VALUES ('dup', ?, 'subscription', 2, 0, 0)", (f"{source_server}/to-list",)).lastrowid
    bad = store.conn.execute("INSERT INTO sources (name, url, kind, priority, created_at, updated_at)"
                             " VALUES ('bad', ?, 'subscription', 3, 0, 0)", (f"{source_server}/to-private",)).lastrowid
    off = store.conn.execute("INSERT INTO sources (name, url, kind, enabled, created_at, updated_at)"
                             " VALUES ('off', 'https://off.example/x', 'subscription', 0, 0, 0)").lastrowid
    lan = store.conn.execute("INSERT INTO sources (name, url, kind, priority, created_at, updated_at)"
                             " VALUES ('lan node', 'vless://11111111-2222-3333-4444-555555555555@192.168.1.1:443"
                             "?security=none#lan', 'vless', 0, 0, 0)").lastrowid
    store.run("INSERT INTO meta (key, value) VALUES (?, '1')", (sources.IMPORTED_KEY,))
    nodes, results = run(VLESSScannerManager()._collect())
    assert len(nodes) == 3  # the three unique links; the LAN link is dropped
    by_id = {r.id: r for r in results}
    assert off not in by_id
    assert (by_id[good].vless_count, by_id[good].valid_count, by_id[good].unique_count) == (3, 3, 3)
    assert (by_id[dup].valid_count, by_id[dup].unique_count) == (3, 0)  # all already seen
    assert not by_id[bad].success and by_id[bad].error.startswith("blocked")
    assert by_id[lan].valid_count == 0
    assert sources.get(bad)["last_status"] == "error" and sources.get(good)["unique_count"] == 3
    # Within its refresh interval the good list is served from the local copy.
    nodes, results = run(VLESSScannerManager()._collect())
    assert {r.id: r for r in results}[good].cached is True and len(nodes) == 3


def test_no_enabled_sources_fails_the_scan():
    sources.db.store().run("INSERT INTO meta (key, value) VALUES (?, '1')", (sources.IMPORTED_KEY,))
    from app.services.scanner import ScanFailed

    with pytest.raises(ScanFailed):
        run(VLESSScannerManager()._collect())
