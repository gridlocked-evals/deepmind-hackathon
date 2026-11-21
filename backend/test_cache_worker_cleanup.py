"""Regression tests for scripts/cache_demo_footage.py pin cleanup."""
import importlib.util
from pathlib import Path
import sys
import threading

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cache_demo_footage.py"


def load_script():
    spec = importlib.util.spec_from_file_location("cache_demo_footage", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_failed_run_releases_pins_from_unconsumed_snapshot_workers(tmp_path, monkeypatch):
    script = load_script()
    pins, unpins = set(), set()
    dot1_running = threading.Event()

    def fake_call(base, path, body=None, token="", timeout=60, method=None):
        if path == "/api/vision/cameras":
            return []
        if path == "/api/cameras":
            return {"cameras": [{"id": f"dot{i}", "kind": "snapshot"} for i in range(4)]}
        if path == "/api/captures/snapshots/dot0":
            assert dot1_running.wait(5)  # dot1 is in flight, so it cannot be cancelled and will pin
            raise ValueError("unexpected worker failure")
        if path == "/api/captures/snapshots/dot1":
            dot1_running.set()
        if path.startswith("/api/captures/snapshots/"):
            return {"capture": {"id": "cap-" + path.rsplit("/", 1)[1]}}
        if method == "DELETE":
            unpins.add(path.split("/")[3])
            return None
        if path.endswith("/pins"):
            pins.add(path.split("/")[3])
        return {}

    monkeypatch.setattr(script, "call", fake_call)
    monkeypatch.setattr(script, "OUT", tmp_path / "demo-cache.json")
    monkeypatch.setattr(sys, "argv", ["x", "--record-seconds", "0.01", "--clip-seconds", "0.01", "--snapshot-workers", "2"])

    with pytest.raises(ValueError):
        script.main()
    # dot0 raised first, so dot1's result never reached kept; its pin must still be released.
    assert "cap-dot1" in pins
    assert unpins == pins
