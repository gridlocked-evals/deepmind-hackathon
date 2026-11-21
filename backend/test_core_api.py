"""Exercise lifecycle and new routes through the actual integrated FastAPI app."""
from fastapi.testclient import TestClient

from backend.main import app


def test_core_api_reports_missing_keys_and_real_history_bounds(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("WORKSHOP_API_KEY", "")
    monkeypatch.setenv("GEMINI_KEY_PROFILE", "primary")
    monkeypatch.setenv("GENIE_ENABLED", "false")
    monkeypatch.setenv("CAPTURE_STORAGE_DIR", str(tmp_path / "captures"))
    monkeypatch.setenv("OBSERVATION_STORAGE_DIR", str(tmp_path / "observations"))
    monkeypatch.setenv("MODEL_LEDGER_PATH", str(tmp_path / "gateway.sqlite3"))
    with TestClient(app) as client:
        status = client.get("/api/models/status")
        assert status.status_code == 200
        value = status.json()
        assert value["key_profile"] == "primary"
        assert len(value["services"]) == 5
        assert all(item["state"] == "unavailable" for item in value["services"])
        assert all(not item["configured"] for item in value["services"])
        assert all("unmeasured" in item["project_quota"] for item in value["services"])
        history = client.get("/api/captures/511ny-32998/buffer").json()
        assert history["bufferedSeconds"] == 0
        assert history["history"]["maxSecondsAgo"] == 0
        assert history["availability"] == "not_recording"
        unavailable = client.post("/api/captures", json={
            "cameraId": "511ny-32998", "secondsAgo": 30, "durationS": 30,
        })
        assert unavailable.status_code == 409
        assert client.post("/api/captures/not-registered/recording").status_code == 404
        monkeypatch.setenv("WORLD_API_TOKEN", "operator-test-token-" + "x" * 32)
        client.headers["Authorization"] = "Bearer operator-test-token-" + "x" * 32
        observation = client.post("/api/observations/extract", json={
            "captureId": "cap_missing", "cameraId": "511ny-32998"})
        assert observation.status_code == 404
        assert client.get("/api/models/status").json()["budgets"]["charged_usd"] == 0
        assert client.post("/api/observations/extract", json={
            "captureId": "cap_missing", "cameraId": "511ny-32998", "apiKey": "client-secret"}).status_code == 422
        assert client.get("/api/contracts/element119").json()["schemaVersion"] == "element119.v1"


def test_workshop_status_does_not_expose_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "primary-test-secret")
    monkeypatch.setenv("WORKSHOP_API_KEY", "workshop-test-secret")
    monkeypatch.setenv("GEMINI_KEY_PROFILE", "workshop")
    monkeypatch.setenv("GENIE_ENABLED", "false")
    monkeypatch.setenv("CAPTURE_STORAGE_DIR", str(tmp_path / "captures"))
    monkeypatch.setenv("OBSERVATION_STORAGE_DIR", str(tmp_path / "observations"))
    monkeypatch.setenv("MODEL_LEDGER_PATH", str(tmp_path / "gateway.sqlite3"))
    with TestClient(app) as client:
        response = client.get("/api/models/status")
        assert response.json()["key_profile"] == "workshop"
        services = response.json()["services"]
        assert all(item["state"] == "unverified" for item in services if item["service"] != "genie")
        genie = next(item for item in services if item["service"] == "genie")
        assert genie["enabled"] is False
        assert genie["optional"] is True
        assert genie["reason"] == "disabled"
        assert "primary-test-secret" not in response.text
        assert "workshop-test-secret" not in response.text
        preflight = client.options("/api/captures/episodes", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        })
        assert preflight.status_code == 200
