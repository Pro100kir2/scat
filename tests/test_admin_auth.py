"""Admin authentication and authorization: every /api/admin route, the session cookie, CSRF, CORS."""
import re

import pytest
from fastapi.testclient import TestClient

from app import config
from app.admin import auth
from app.main import app

TOKEN = "admin-" + "k" * 40
ORIGIN = "http://localhost:5173"


@pytest.fixture
def client(api_service, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    monkeypatch.setattr(config, "PUBLIC_API_URL", "")  # local http development: no Secure flag
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", [ORIGIN])
    return TestClient(app)


def admin_routes():
    """Every admin route the application actually serves (from its OpenAPI schema, which
    includes every admin route), with a concrete path."""
    out = []
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api/admin"):
            continue
        for method in sorted(m.upper() for m in operations):
            concrete = re.sub(r"\{probe_id\}", "prb_0123456789abcdef", path)
            concrete = re.sub(r"\{(source_id|scan_id)\}", "1", concrete)
            concrete = concrete.replace("{action}", "clear" if "cache" in path else "start" if "scanner" in path
                                        else "approve").replace("{key}", "TOP_COUNT")
            out.append((method, concrete))
    return out


ROUTES = admin_routes()
PUBLIC_ADMIN_ROUTES = {("POST", "/api/admin/session")}  # login itself


def test_the_route_list_is_complete():
    assert len(ROUTES) > 35  # a sanity check that enumeration found the admin API


@pytest.mark.parametrize("method,path", [r for r in ROUTES if r not in PUBLIC_ADMIN_ROUTES])
def test_every_admin_route_requires_authentication(client, method, path):
    assert client.request(method, path, json={}).status_code == 401
    assert client.request(method, path, json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.request(method, path, json={}, headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401


@pytest.mark.parametrize("method,path", ROUTES)
def test_admin_api_is_off_without_a_token(api_service, monkeypatch, method, path):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    assert TestClient(app).request(method, path, json={}, headers={"Authorization": "Bearer "}).status_code == 404


def login(client, token=TOKEN, **headers):
    return client.post("/api/admin/session", json={"token": token},
                       headers={"X-Scat-Admin": "1", "Origin": ORIGIN, **headers})


def test_login_sets_a_strict_http_only_cookie_and_never_returns_the_token(client):
    reply = login(client)
    assert reply.status_code == 200 and TOKEN not in reply.text
    cookie = reply.headers["set-cookie"]
    assert cookie.startswith(f"{auth.COOKIE}=") and TOKEN not in cookie
    for flag in ("HttpOnly", "SameSite=strict", "Path=/api/admin"):
        assert flag.lower() in cookie.lower()
    stored = auth.db.store().all("SELECT id_hash FROM admin_sessions")
    session_id = cookie.split(";")[0].split("=", 1)[1]
    assert len(stored) == 1 and session_id not in stored[0]["id_hash"]  # only a hash is stored


def test_secure_flag_when_served_over_https(client, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_API_URL", "https://api.scat-search.ru")
    assert "secure" in login(client).headers["set-cookie"].lower()


def test_login_rejections(client):
    assert login(client, token="wrong").status_code == 401
    assert client.post("/api/admin/session", json={"token": TOKEN}, headers={"Origin": ORIGIN}).status_code == 403
    assert login(client, Origin="https://evil.example").status_code == 403
    events = auth.db.store().all("SELECT kind FROM events WHERE component='auth'")
    assert {"kind": "admin_login_failed"} in events


def test_login_is_rate_limited(client):
    codes = [login(client, token="wrong").status_code for _ in range(12)]
    assert codes[-1] == 429


def test_session_cookie_needs_the_admin_header_and_an_allowed_origin(client):
    login(client)
    assert client.get("/api/admin/dashboard").status_code == 403  # cookie alone: a CSRF-shaped request
    assert client.get("/api/admin/dashboard", headers={"X-Scat-Admin": "1"}).status_code == 200
    assert client.post("/api/admin/cache/clear", headers={"X-Scat-Admin": "1", "Origin": "https://evil.example"}
                       ).status_code == 403
    assert client.post("/api/admin/cache/clear", headers={"X-Scat-Admin": "1", "Origin": ORIGIN}).status_code == 200


def test_logout_and_token_rotation_end_sessions(client, monkeypatch):
    login(client)
    h = {"X-Scat-Admin": "1"}
    assert client.get("/api/admin/session", headers=h).status_code == 200
    monkeypatch.setattr(config, "ADMIN_TOKEN", "rotated-" + "z" * 40)
    assert client.get("/api/admin/session", headers=h).status_code == 401
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    assert client.get("/api/admin/session", headers=h).status_code == 200
    client.delete("/api/admin/session", headers=h)
    client.cookies.set(auth.COOKIE, "stale", path="/api/admin")
    assert client.get("/api/admin/session", headers=h).status_code == 401


def test_idle_sessions_expire(client, monkeypatch):
    login(client)
    auth.db.store().run("UPDATE admin_sessions SET last_seen = last_seen - ?", (config.ADMIN_SESSION_IDLE_SECONDS + 5,))
    assert client.get("/api/admin/session", headers={"X-Scat-Admin": "1"}).status_code == 401


def test_bearer_token_still_works_for_scripts(client):
    reply = client.post("/api/admin/cache/clear", headers={"Authorization": f"Bearer {TOKEN}"})
    assert reply.json() == {"status": "cleared"}


def test_cors_credentials_only_on_the_admin_api(client, monkeypatch):
    preflight = {"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "content-type,x-scat-admin"}
    admin = client.options("/api/admin/probes", headers=preflight)
    assert admin.headers.get("access-control-allow-credentials") == "true"
    assert "x-scat-admin" in admin.headers.get("access-control-allow-headers", "").lower()
    public = client.get("/api/health", headers={"Origin": ORIGIN})
    assert "access-control-allow-credentials" not in public.headers
    evil = client.options("/api/admin/probes", headers={**preflight, "Origin": "https://evil.example"})
    assert evil.headers.get("access-control-allow-origin") is None


def test_admin_responses_never_contain_secrets(client, probe_service):
    enrollment = probe_service.create_enrollment(name="МТС", country="RU")
    creds = probe_service.register(enrollment["token"], agent_version="1.0.0", profile="linux-amd64",
                                   platform="linux/amd64", public_ip="198.51.100.1")
    h = {"Authorization": f"Bearer {TOKEN}"}
    bodies = "".join(client.get(p, headers=h).text for p in (
        "/api/admin/probes", f"/api/admin/probes/{creds['probe_id']}", "/api/admin/settings", "/api/admin/health",
        "/api/admin/dashboard", "/api/admin/events", f"/api/admin/probes/{creds['probe_id']}/jobs"))
    for secret in (TOKEN, creds["secret"], enrollment["token"]):
        assert secret not in bodies
    assert "secret_hash" not in bodies and "token_hash" not in bodies


def test_bodyless_admin_actions_work_like_the_documented_curl_commands(client):
    """`curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" .../cache/clear` sends no Content-Length."""
    from app.main import MAX_BODY_BYTES

    h = {"Authorization": f"Bearer {TOKEN}"}
    request = client.build_request("POST", "/api/admin/cache/clear", headers=h)
    del request.headers["content-length"]
    assert client.send(request).status_code == 200
    big = client.post("/api/admin/sources", headers=h, content=b"{" + b" " * (MAX_BODY_BYTES + 1) + b"}")
    assert big.status_code == 413
