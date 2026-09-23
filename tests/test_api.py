import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_home_page():
    response = client.get("/")
    assert response.status_code == 200
    assert "VLESS Speed Finder" in response.text


def test_api_status():
    response = client.get("/api/scan/status")
    assert response.status_code == 200
    data = response.json()
    assert "is_scanning" in data
    assert "progress" in data


def test_api_sources():
    response = client.get("/api/sources")
    assert response.status_code == 200
    data = response.json()
    assert "sources" in data
    assert len(data["sources"]) > 0


def test_api_vless_top():
    response = client.get("/api/vless/top")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "nodes" in data


def test_api_vless_sub():
    response = client.get("/api/vless/sub")
    assert response.status_code == 200
    assert "Subscription-Userinfo" in response.headers
