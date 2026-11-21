"""Exercise lifecycle and new routes through the actual integrated FastAPI app."""
from fastapi.testclient import TestClient

from backend.main import app


def test_core_api_reports_missing_keys_and_real_history_bounds(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("WORKSHOP_API_KEY", "")
    monkeypatch.setenv("GEMINI_KEY_PROFILE", "primary")
    monkeypatch.setenv("CAPTURE_STORAGE_DIR", str(tmp_path / "captures"))
    monkeypatch.setenv("MODEL_LEDGER_PATH", str(tmp_path / "gateway.sqlite3"))
    with TestClient(app) as client:
        status = client.get("/api/models/status")
        assert status.status_code == 200
        value = status.json()
        assert value["keyProfile"] == "primary"
        assert len(value["services"]) == 5
        assert all(item["status"] == "unavailable" for item in value["services"])
        assert all(not item["configured"] for item in value["services"])
        assert value["quotaMeasured"] is False
        history = client.get("/api/captures/sources/511ny-32998/history").json()
        assert history["earliestSeconds"] is None
        assert history["latestSeconds"] is None
        assert history["recording"] is False
        unavailable = client.post("/api/captures/episodes", json={
            "sourceId": "511ny-32998", "recordingId": history["recordingId"],
            "startSeconds": 0, "endSeconds": 30,
        })
        assert unavailable.status_code == 409
        assert client.post("/api/captures/sources/not-registered/start").status_code == 404


def test_workshop_status_does_not_expose_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "primary-test-secret")
    monkeypatch.setenv("WORKSHOP_API_KEY", "workshop-test-secret")
    monkeypatch.setenv("GEMINI_KEY_PROFILE", "workshop")
    monkeypatch.setenv("CAPTURE_STORAGE_DIR", str(tmp_path / "captures"))
    monkeypatch.setenv("MODEL_LEDGER_PATH", str(tmp_path / "gateway.sqlite3"))
    with TestClient(app) as client:
        response = client.get("/api/models/status")
        assert response.json()["keyProfile"] == "workshop"
        assert all(item["status"] == "configured" for item in response.json()["services"])
        assert "primary-test-secret" not in response.text
        assert "workshop-test-secret" not in response.text
        preflight = client.options("/api/captures/episodes", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        })
        assert preflight.status_code == 200
