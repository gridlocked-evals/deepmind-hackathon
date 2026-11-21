"""scripts/cache_demo_footage.py must not release the published index's pins before replacing it."""
import importlib.util
import json
from pathlib import Path
import sys
from threading import Event

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

    def cache_video(base, cameras, args, kept, pins):
        kept["new-cam"] = {"kind": "hls", "saved": {"capture": {"id": "new"}}}  # pinned by the real function
        pins.remember(kept["new-cam"]["saved"])
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


def test_failed_first_worker_releases_unconsumed_later_workers_pin(tmp_path, monkeypatch):
    module, out, _ = load(tmp_path, monkeypatch)
    before = out.read_text()
    pinned, completed = {"old"}, set()
    later_pinned = Event()

    def call(base, path, body=None, **kwargs):
        if path == "/api/cameras":
            return {"cameras": [{"id": "first", "kind": "snapshot"}, {"id": "later", "kind": "snapshot"}]}
        if path == "/api/captures/snapshots/first":
            assert later_pinned.wait(2), "second worker did not pin its capture"
            return {}  # malformed result makes the first worker fail before map consumes the second result
        if path == "/api/captures/snapshots/later":
            return {"capture": {"id": "later-capture"}}
        if path == "/api/captures/later-capture/pins":
            pinned.add("later-capture")
            later_pinned.set()
            return {}
        if kwargs.get("method") == "DELETE":
            assert completed == {"first", "later"}, "rollback must run after both workers finish"
            pinned.discard(path.split("/")[3])
            return {}
        assert path in ("/api/health", "/api/vision/cameras")
        return {"cameras": []}

    original_snapshot = module.cache_snapshot
    def snapshot(base, camera, *args):
        try:
            return original_snapshot(base, camera, *args)
        finally:
            completed.add(camera["id"])
    monkeypatch.setattr(module, "call", call)
    monkeypatch.setattr(module, "cache_video", lambda base, cameras, args, kept, *rest: (kept, {}))
    monkeypatch.setattr(module, "cache_snapshot", snapshot)
    with pytest.raises(KeyError):
        module.main()
    assert out.read_text() == before
    assert completed == {"first", "later"}
    assert pinned == {"old"}


@pytest.mark.parametrize("failure", ["pin_response", "publication"])
def test_pin_attempts_are_released_unless_referenced_by_published_index(tmp_path, monkeypatch, failure):
    module, out, _ = load(tmp_path, monkeypatch)
    before = out.read_text()
    pinned = {"old"}

    def call(base, path, body=None, **kwargs):
        if path == "/api/cameras":
            return {"cameras": [{"id": "uncertain", "kind": "snapshot"}, {"id": "good", "kind": "snapshot"}]}
        if path.startswith("/api/captures/snapshots/"):
            return {"capture": {"id": path.rsplit("/", 1)[1]}}
        if path.endswith("/pins"):
            identifier = path.split("/")[3]
            pinned.add(identifier)
            if failure == "pin_response" and identifier == "uncertain":
                raise RuntimeError("pin persisted, but its response was lost")
            return {}
        if kwargs.get("method") == "DELETE":
            if failure == "pin_response":
                assert set(json.loads(out.read_text())["cameras"]) == {"good"}
            else:
                assert out.read_text() == before
            pinned.discard(path.split("/")[3])
            return {}
        assert path in ("/api/health", "/api/vision/cameras")
        return {"cameras": []}

    monkeypatch.setattr(module, "call", call)
    monkeypatch.setattr(module, "cache_video", lambda base, cameras, args, kept, pins: (kept, {}))
    if failure == "publication":
        def fail_replace(path, destination):
            raise OSError("fixture publication failure")
        monkeypatch.setattr(Path, "replace", fail_replace)
        with pytest.raises(OSError, match="fixture publication failure"):
            module.main()
        assert out.read_text() == before and pinned == {"old"}
    else:
        assert module.main() == 0
        assert pinned == {"good"}
