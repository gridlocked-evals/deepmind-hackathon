import json

import pytest
from pydantic import ValidationError

from backend.evaluation.manifest import (
    Attempt, BaselineDefinition, Episode, FinalRun, HoldoutError, Manifest, Outcome, ResultsLedger,
    add_fresh_capture, lock, read_source, report, split_report, verify_lock,
)
from backend.gateway.contracts import digest

H = "a" * 64


def ep(episode_id, split="dev", source="cam1", window="w1", **extra):
    fields = dict(episode_id=episode_id, source_id=source, provider="nyc_dot", modality="continuous",
                  source_hashes=(extra.pop("hash", H),), window_id=window, duration_s=60, split=split)
    return Episode(**(fields | extra))


def manifest(*episodes):
    return Manifest(episodes=episodes or (ep("d1"),), prompts={"observation_v1": H},
                    thresholds={"image_gate": .8}, attack_suite_sha256="b" * 64)


def test_episode_rules():
    assert ep("ok", duration_s=120).duration_s == 120
    with pytest.raises(ValidationError):
        ep("long", duration_s=120.1)
    for provider in ("DemoScenarioProvider", "fixture"):
        with pytest.raises(ValidationError, match="excluded"):
            ep("demo", provider=provider)


def test_derivative_inherits_parent_split():
    manifest(ep("t1", "test"), ep("t1_crop", "test", parent_id="t1"))
    with pytest.raises(ValidationError, match="differs from parent"):
        manifest(ep("t1", "test"), ep("t1_crop", "dev", parent_id="t1"))
    with pytest.raises(ValidationError, match="unknown parent"):
        manifest(ep("x", parent_id="missing"))


def test_split_report_lists_shortfalls():
    small = split_report([ep("d1"), ep("t1", "test")])
    assert small["dev"] == 1 and small["test"] == 1
    assert "dev: 1 < 4" in small["shortfalls"] and "no test camera absent from development" in small["shortfalls"]
    full = [ep(f"d{i}", source=f"cam{i % 2}", window=f"w{i % 2}") for i in range(4)]
    full += [ep(f"t{i}", "test", source=f"cam{i % 3}") for i in range(8)]
    assert split_report(full)["shortfalls"] == []
    assert split_report(full)["test_cameras_absent_from_dev"] == ["cam2"]


def test_lock_detects_tampering_and_allows_labelled_fresh_capture(tmp_path):
    path = tmp_path / "manifest.lock.json"
    pinned = lock(manifest(ep("d1"), ep("t1", "test")), path)
    assert verify_lock(path, pinned).sha256 == pinned
    with pytest.raises(FileExistsError):
        lock(manifest(), path)

    fresh = add_fresh_capture(path, ep("fresh", "test", hash="c" * 64), pinned)
    assert fresh.additions[0].label == "post_freeze_judge_selected_fresh_capture"
    assert verify_lock(path, pinned).sha256 == pinned
    with pytest.raises(ValueError, match="test episode"):
        add_fresh_capture(path, ep("fresh_dev", hash="d" * 64), pinned)

    data = json.loads(path.read_text())
    data["manifest"]["thresholds"]["image_gate"] = .5
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="changed after locking"):
        verify_lock(path)
    # Re-hashing the file in place is still caught against the pinned hash.
    data["sha256"] = Manifest.model_validate(data["manifest"]).sha256
    path.write_text(json.dumps(data))
    verify_lock(path)
    with pytest.raises(ValueError, match="changed after locking"):
        verify_lock(path, pinned)


def test_holdout_needs_final_run(tmp_path):
    dev_bytes, test_bytes = b"dev clip", b"test clip"
    (tmp_path / "dev.ts").write_bytes(dev_bytes)
    (tmp_path / "test.ts").write_bytes(test_bytes)
    m = manifest(ep("d1", hash=digest(dev_bytes)), ep("t1", "test", hash=digest(test_bytes)))
    path = tmp_path / "lock.json"
    pinned = lock(m, path)

    assert read_source(tmp_path / "dev.ts", m)[0].episode_id == "d1"
    with pytest.raises(HoldoutError):
        read_source(tmp_path / "test.ts", m)
    with pytest.raises(ValueError):
        FinalRun(path, "0" * 64)
    episode, data = read_source(tmp_path / "test.ts", m, FinalRun(path, pinned))
    assert episode.episode_id == "t1" and data == test_bytes


def test_ledger_keeps_failures_and_report_has_full_denominators(tmp_path):
    ledger = ResultsLedger(tmp_path / "results.jsonl")
    for outcome in Outcome:
        ledger.append(Attempt(episode_id="t1", system="full", outcome=outcome, cost_usd=.01, latency_ms=10))
    ledger.append(Attempt(episode_id="t1", system="baseline", outcome="success", cost_usd=.002,
                          latency_ms=5, stochastic=True))
    rows = ledger.read()
    assert len(rows) == len(Outcome) + 1

    result = report(rows)
    full = result["full"]
    assert full["attempts"] == 8 and all(n == 1 for n in full["counts"].values())
    assert full["eligible"] == 7 and full["success_rate"] == 1 / 7
    assert full["ci95"][0] < 1 / 7 < full["ci95"][1]
    assert result["baseline"]["counts"]["error"] == 0
    assert "not the same worlds" in result["baseline"]["trials"]


def test_baseline_is_single_hydrogen_call():
    baseline = BaselineDefinition(prompt_id="observation_v1", input_sha256=H, schema_sha256=H,
                                  calibration_sha256=H)
    assert baseline.model_id == "gemini-4-hydrogen" and baseline.added_calls is None
    with pytest.raises(ValidationError):
        BaselineDefinition(prompt_id="p", input_sha256=H, schema_sha256=H, calibration_sha256=H, calls=2)
