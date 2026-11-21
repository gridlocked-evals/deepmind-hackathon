"""Keep every backend test isolated from developer credentials and retained evidence."""
import json

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "WORKSHOP_API_KEY", "WORLD_API_TOKEN"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("GEMINI_KEY_PROFILE", "primary")
    monkeypatch.setenv("GENIE_ENABLED", "false")
    monkeypatch.setenv("CAPTURE_STORAGE_DIR", str(tmp_path / "captures"))
    monkeypatch.setenv("OBSERVATION_STORAGE_DIR", str(tmp_path / "observations"))
    monkeypatch.setenv("CAPTURE_DIR", str(tmp_path / "captures"))
    monkeypatch.setenv("PIPELINE_DIR", str(tmp_path / "pipeline"))
    monkeypatch.setenv("MODEL_LEDGER_PATH", str(tmp_path / "legacy.sqlite3"))
    configuration = tmp_path / "gateway.json"
    configuration.write_text(json.dumps({"data_dir": str(tmp_path / "gateway")}))
    monkeypatch.setenv("GATEWAY_CONFIG", str(configuration))
