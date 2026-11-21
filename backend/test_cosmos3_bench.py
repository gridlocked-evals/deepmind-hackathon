import importlib.util
import json
import pathlib
import sys
import types

import pytest

BENCH = pathlib.Path(__file__).parent / "worlds" / "cosmos3_h100" / "bench.py"


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.text = body, status, json.dumps(body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.body


def load_bench(monkeypatch, tmp_path, submit, status):
    class Session:
        trust_env = True

        def post(self, *a, **k):
            return submit

        def get(self, *a, **k):
            return Resp({"status": status})

    # requests is only installed in the H100 env, not the backend test env
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(Session=Session))
    spec = importlib.util.spec_from_file_location("cosmos3_bench", BENCH)
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    (tmp_path / "captions").mkdir()
    caption = {"caption_structured": {"chunk_0_300": {"caption": "a street"}}}
    (tmp_path / "captions" / "camera_front_wide_120fov.json").write_text(json.dumps(caption))
    monkeypatch.setattr(bench, "SAMPLE", str(tmp_path))
    monkeypatch.setattr(bench, "env_info", lambda: {})
    monkeypatch.setattr(bench, "sh", lambda cmd: "")
    monkeypatch.setattr(sys, "argv", ["bench.py", "--port", "1", "--out", str(tmp_path / "out")])
    return bench


@pytest.mark.parametrize(
    "submit,status",
    [(Resp({"id": "v1"}), "failed"), (Resp({"detail": "boom"}, 500), "completed")],
)
def test_failed_generation_exits_nonzero_and_keeps_diagnostics(monkeypatch, tmp_path, submit, status):
    bench = load_bench(monkeypatch, tmp_path, submit, status)

    assert bench.main() == 1
    assert (tmp_path / "out" / "bench_result.json").exists()
