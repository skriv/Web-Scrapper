from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app import main
from app.security import UnsafeURL
from app.service import ImportError

client = TestClient(main.app)


def test_health_and_demo_use_no_network():
    assert client.get("/api/health").json()["status"] == "ok"
    response = client.get("/api/demo")
    assert response.status_code == 200
    data = response.json()
    assert data["profile"]["businessName"] == "Northline Dental"
    assert data["profile"]["timezone"] == "America/Los_Angeles"
    assert data["profile"]["services"]
    assert "Demo" in data["warnings"][0]
    assert response.headers["cache-control"] == "no-store"


def test_extract_wires_user_options(monkeypatch):
    handler = AsyncMock(return_value={"profile": {"businessName": "Clinic"}})
    monkeypatch.setattr(main, "import_profile", handler)
    response = client.post("/api/extract", json={"url": "https://example.com", "renderJs": False, "followLinks": False})
    assert response.status_code == 200
    handler.assert_awaited_once_with("https://example.com", False, False)


def test_unsafe_url_is_clear_client_error(monkeypatch):
    monkeypatch.setattr(main, "import_profile", AsyncMock(side_effect=UnsafeURL("private address")))
    response = client.post("/api/extract", json={"url": "http://127.0.0.1"})
    assert response.status_code == 400
    assert response.json()["detail"] == "private address"


def test_import_failure_is_recoverable(monkeypatch):
    monkeypatch.setattr(main, "import_profile", AsyncMock(side_effect=ImportError("site unavailable")))
    assert client.post("/api/extract", json={"url": "https://example.com"}).status_code == 422


def test_payload_limits_and_validation():
    assert client.post("/api/extract", json={"url": ""}).status_code == 422
    assert client.post("/api/extract", json={"url": "https://example.com", "apiKey": "unused"}).status_code == 422
    assert client.post("/api/extract", content="x" * 9000).status_code == 413


def test_no_internal_exception_detail_leaks(monkeypatch):
    monkeypatch.setattr(main, "import_profile", AsyncMock(side_effect=RuntimeError("secret diagnostic")))
    response = client.post("/api/extract", json={"url": "https://example.com"})
    assert response.status_code == 502
    assert "secret diagnostic" not in response.text
