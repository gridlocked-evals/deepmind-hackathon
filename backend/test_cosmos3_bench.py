import importlib.util
import json
import pathlib
import sys
import types

import subprocess

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


@pytest.mark.parametrize("bench_rc,expected", [(1, 1), (0, 0)])
def test_bench_running_wrapper_exit_code_reflects_bench(tmp_path, bench_rc, expected):
    root = tmp_path / "spike"
    (root / "logs").mkdir(parents=True)
    (root / "logs" / "serve_gpu0.log").write_text("server up\n")
    fake = tmp_path / "env" / "bin" / "python"
    fake.parent.mkdir(parents=True)
    fake.write_text(f"#!/bin/sh\nexit {bench_rc}\n")  # stands in for bench.py
    fake.chmod(0o755)
    script = BENCH.parent / "bench_running.sh"
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "COSMOS3_ROOT": str(root), "COSMOS3_ENV": str(tmp_path / "env")}
    done = subprocess.run(["bash", str(script), "0", "1", "61", "t"], env=env, cwd=tmp_path, timeout=30)
    assert done.returncode == expected


def test_serve_and_bench_fails_when_server_never_ready(tmp_path):
    here = tmp_path / "scripts"
    here.mkdir()
    (here / "serve_and_bench.sh").write_text((BENCH.parent / "serve_and_bench.sh").read_text())
    # fake serve.sh: live server process, start epoch, but never a ready file
    (here / "serve.sh").write_text(
        'L=$COSMOS3_ROOT/logs; mkdir -p $L; date +%s > $L/serve_gpu0_start_epoch.txt\n'
        'echo still loading > $L/serve_gpu0.log\n'
        'nohup sleep 30 >/dev/null 2>&1 & echo $! > $L/serve_gpu0.pid\n'
    )
    fake = tmp_path / "env" / "bin" / "python"
    fake.parent.mkdir(parents=True)
    fake.write_text(f"#!/bin/sh\ntouch {tmp_path}/bench_ran\n")
    fake.chmod(0o755)
    root = tmp_path / "spike"
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "COSMOS3_ROOT": str(root),
           "COSMOS3_ENV": str(tmp_path / "env"), "COSMOS3_READY_TIMEOUT_S": "1"}
    try:
        done = subprocess.run(["bash", str(here / "serve_and_bench.sh"), "0", "1"], env=env, cwd=tmp_path, timeout=30)
    finally:
        subprocess.run(["kill", (root / "logs" / "serve_gpu0.pid").read_text().strip()])
    assert done.returncode == 1
    assert not (tmp_path / "bench_ran").exists()
    log = next(root.glob("runs/*/pipeline.log")).read_text()
    assert "SERVER_NOT_READY" in log and "still loading" in log
