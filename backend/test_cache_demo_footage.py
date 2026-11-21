"""scripts/cache_demo_footage.py must not release the published index's pins before replacing it."""
import importlib.util
import json
from pathlib import Path
import sys
import threading

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cache_demo_footage.py"


def load(tmp_path, monkeypatch, fail_on=None):
    spec = importlib.util.spec_from_file_location("cache_demo_footage", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path / "demo-cache.json"
    out.write_text(json.dumps({"cameras": {"old-cam": {"saved": {"capture": {"id": "old"}}}}}))
    monkeypatch.setattr(module, "OUT", out)
    calls = []

    def call(base, path, body=None, token="", timeout=60, method=None):
        calls.append((method, path))
        if path == fail_on:
            raise RuntimeError("boom")
        return {"cameras": []}

    def cache_video(base, cameras, args, kept):
        kept["new-cam"] = {"kind": "hls", "saved": {"capture": {"id": "new"}}}  # pinned by the real function
        return kept, {}

    monkeypatch.setattr(module, "call", call)
    monkeypatch.setattr(module, "cache_video", cache_video)
    monkeypatch.setattr(sys, "argv", ["cache_demo_footage.py"])
    return module, out, calls


def test_failed_run_keeps_old_pins_and_releases_new(tmp_path, monkeypatch):
    module, out, calls = load(tmp_path, monkeypatch, fail_on="/api/cameras")
    before = out.read_text()
    with pytest.raises(RuntimeError):
        module.main()
    deletes = [path for method, path in calls if method == "DELETE"]
    assert deletes == ["/api/captures/new/pins/demo-cache"]
    assert out.read_text() == before


def test_successful_run_releases_old_pins_after_writing_index(tmp_path, monkeypatch):
    module, out, calls = load(tmp_path, monkeypatch)
    assert module.main() == 0
    deletes = [path for method, path in calls if method == "DELETE"]
    assert deletes == ["/api/captures/old/pins/demo-cache"]
    assert "new-cam" in json.loads(out.read_text())["cameras"]


def test_failed_run_releases_pins_from_unconsumed_snapshot_workers(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("cache_demo_footage", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
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
