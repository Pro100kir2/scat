import asyncio
import base64
import time

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture
def client(api_service):
    # No `with`: the lifespan (auto refresh) must not start real scans in tests.
    return TestClient(app)


def fill_cache(service):
    async def scenario():
        service.request_scan()
        await service.wait()

    asyncio.run(scenario())


def test_root(client):
    assert client.get("/").json()["name"] == "Скат API"


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_results_empty_cache_starts_scan(client, api_service):
    data = client.get("/api/results").json()
    assert data["cache_state"] == "empty"
    assert data["nodes"] == []
    assert api_service.stats["scans_started"] == 1


def test_results_from_cache(client, api_service):
    fill_cache(api_service)
    response = client.get("/api/results")
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["cache_state"] == "fresh"
    assert data["is_scanning"] is False
    assert data["cache"]["count"] == 1
    node = data["nodes"][0]
    assert node["rank"] == 1
    assert node["country"] == "DE"  # measured exit country
    assert node["label"] == "Frankfurt"
    assert node["link"].startswith("vless://")
    assert node["latency_ms"] == 120
    assert node["speed_mbps"] == 12.3
    assert node["checks_passed"] == 5 and node["checks_total"] == 5
    assert api_service.scanner.runs == 1


def test_post_scan_with_fresh_cache_is_a_cache_hit(client, api_service):
    fill_cache(api_service)
    data = client.post("/api/scan").json()
    assert data["outcome"] == "cached"
    assert api_service.scanner.runs == 1


def test_public_clear_cache(client, api_service):
    fill_cache(api_service)
    assert api_service.cache_state() == "fresh"
    response = client.post("/api/cache/clear")
    assert response.status_code == 200
    data = response.json()
    assert data["outcome"] == "started"
    assert api_service.cache_state() == "empty"


def test_scan_status(client):
    data = client.get("/api/scan/status").json()
    assert set(data) >= {"cache_state", "is_scanning", "queued", "progress", "cache", "last_scan_failed"}


def test_scan_rate_limit(client, api_service):
    fill_cache(api_service)
    for _ in range(config.SCAN_RATE_LIMIT_PER_MINUTE):
        assert client.post("/api/scan").status_code == 200
    response = client.post("/api/scan")
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


def test_subscription(client, api_service):
    fill_cache(api_service)
    response = client.get("/api/vless/sub")
    assert response.status_code == 200
    assert "Subscription-Userinfo" in response.headers
    assert response.headers["profile-title"].startswith("base64:")
    assert base64.b64decode(response.text).decode().startswith("vless://")


def test_raw_and_top(client, api_service):
    fill_cache(api_service)
    assert client.get("/api/vless/raw").text.startswith("vless://")
    assert client.get("/api/vless/top").json()["count"] == 1


def test_sources(client):
    assert client.get("/api/sources").json()["count"] == 1


def test_admin_disabled_without_token(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    assert client.post("/api/admin/cache/clear").status_code == 404


def test_admin_requires_valid_token(client, api_service, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secret")
    assert client.post("/api/admin/cache/clear").status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert client.post("/api/admin/cache/clear", headers=wrong).status_code == 401
    ok = {"Authorization": "Bearer secret"}
    assert client.post("/api/admin/cache/clear", headers=ok).json() == {"status": "cleared"}


def test_cors_allows_configured_origin(client):
    origin = config.ALLOWED_ORIGINS[0]
    response = client.get("/api/health", headers={"Origin": origin})
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers


def test_cors_rejects_unknown_origin(client):
    response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight(client):
    origin = config.ALLOWED_ORIGINS[0]
    response = client.options(
        "/api/scan",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_country_search_starts_scan_then_serves_cache(api_service, monkeypatch):
    monkeypatch.setattr(config, "AUTO_REFRESH", False)
    monkeypatch.setattr(config, "AUTO_SCAN_ON_STARTUP", False)
    with TestClient(app) as client:  # keeps one event loop so the background scan can finish
        first = client.get("/api/vless/search/de")
        assert first.status_code == 202
        body = first.json()
        assert body["country"] == "DE" and body["outcome"] == "started" and body["nodes"] == []

        for _ in range(50):  # poll like the browser would
            second = client.get("/api/vless/search/DE")
            if second.status_code == 200:
                break
            time.sleep(0.02)
    assert second.status_code == 200
    data = second.json()
    assert data["outcome"] == "cached"
    assert data["cache"]["country"] == "DE"
    assert data["nodes"][0]["rank"] == 1
    assert api_service.scanner.runs == 1
    # The main results are a separate query and were not touched.
    assert api_service.cache_state() == "empty"


@pytest.mark.parametrize("bad", ["D", "DEU", "1A", "d-"])
def test_country_search_validates_code(client, bad):
    assert client.get(f"/api/vless/search/{bad}").status_code == 422


def test_country_search_polling_is_not_rate_limited(client, api_service):
    fill_cache(api_service)
    asyncio.run(_fill_country(api_service, "DE"))
    for _ in range(config.SCAN_RATE_LIMIT_PER_MINUTE * 3):
        assert client.get("/api/vless/search/DE").status_code == 200


def test_country_search_busy(client, api_service, monkeypatch):
    monkeypatch.setattr(api_service, "request_scan", lambda query: "busy")
    response = client.get("/api/vless/search/NL")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"


async def _fill_country(service, country):
    from app.services.scanner import ScanQuery

    service.request_scan(ScanQuery(country))
    await service.wait(ScanQuery(country))
