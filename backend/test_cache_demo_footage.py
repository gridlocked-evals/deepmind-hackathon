"""scripts/cache_demo_footage.py must not release the published index's pins before replacing it."""
import importlib.util
import json
from pathlib import Path
import sys

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
