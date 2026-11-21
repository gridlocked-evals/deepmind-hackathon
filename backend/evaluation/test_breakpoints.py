import hashlib
import json

import pytest

from backend.evaluation.breakpoints import (
    FailureEntry, FailureLedger, PassCriterion, SettingResult, Sweep, SweepLog, classify, find_breakpoint,
    media_ref, paired_bootstrap,
)
from backend.evaluation.manifest import Outcome


def sweep(**extra):
    fields = dict(sweep_id="blur_v1", dimension="visual_degradation", settings=(0.0, 0.25, 0.5, 0.75),
                  baseline={"input_length_s": 60, "style": "photo"},
                  criterion=PassCriterion(metric="observation_quality", comparator=">=", threshold=.8))
    return Sweep(**(fields | extra))


def res(s, setting, **metrics):
    return SettingResult(sweep_sha256=s.sha256, setting=setting, **metrics)


def test_freeze_and_hash_rejection(tmp_path):
    s, log = sweep(), SweepLog(tmp_path / "sweep.jsonl")
    sha = log.freeze(s)
    with pytest.raises(FileExistsError):
        log.freeze(s)
    assert sweep(baseline={"input_length_s": 30, "style": "photo"}).sha256 != sha  # baseline is hashed
    log.record(res(s, 0.0, observation_quality=.9))
    with pytest.raises(ValueError, match="different sweep hash"):
        log.record(SettingResult(sweep_sha256="0" * 64, setting=0.25))
    with pytest.raises(ValueError, match="not predeclared"):
        log.record(res(s, 0.3))
    with pytest.raises(ValueError, match="no reruns"):
        log.record(res(s, 0.0, observation_quality=.95))
    with pytest.raises(ValueError, match="swept dimension"):
        sweep(baseline={"visual_degradation": 0})
    lines = log.path.read_text().splitlines()
    header = json.loads(lines[0])
    header["sweep"]["criterion"]["threshold"] = .5
    log.path.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n")
    with pytest.raises(ValueError, match="changed after freezing"):
        log.sweep()


def test_abstention_is_not_false_acceptance():
    s = sweep()
    assert classify(s, res(s, .5, observation_quality=.3, counts={Outcome.ABSTAINED: 4})) == (
        "fail", "abstained_at_information_limit")
    assert classify(s, res(s, .5, observation_quality=.95, false_acceptance=.1)) == ("fail", "unsafe_false_acceptance")


def test_outage_is_inconclusive_not_breakpoint():
    s = sweep()
    results = [res(s, 0.0, observation_quality=.9),
               res(s, .25, observation_quality=.1, counts={Outcome.ERROR: 2}),
               res(s, .5, observation_quality=.1, provider_outage=True)]
    assert classify(s, results[1]) == ("inconclusive", "provider_outage")
    bp = find_breakpoint(s, results)
    assert bp.status == "incomplete_within_tested_range" and bp.first_fail is None
    assert bp.last_pass == 0.0 and bp.inconclusive == [.25, .5] and bp.untested == [.75]


def test_breakpoint_found_and_incomplete():
    s = sweep()
    passing = [res(s, x, observation_quality=.9) for x in s.settings]
    assert find_breakpoint(s, passing).status == "incomplete_within_tested_range"
    bp = find_breakpoint(s, passing[:2] + [res(s, .5, observation_quality=.6)])
    assert (bp.status, bp.last_pass, bp.first_fail) == ("found", .25, .5)
    assert bp.reason.startswith("observation_quality_0.6")


def test_failure_ledger_append_only(tmp_path):
    media = tmp_path / "frame.jpg"
    media.write_bytes(b"jpeg")
    ledger, s = FailureLedger(tmp_path / "failures.jsonl"), sweep()
    first = FailureEntry(sweep_sha256=s.sha256, setting=.5, reason="unsafe_false_acceptance",
                         metrics={"false_acceptance": .1}, media=(media_ref(media),), remaining_limits="fog untested")
    ledger.append(first)
    ledger.append(first.model_copy(update={"change": "prompt v2", "remaining_limits": "night untested"}))
    entries = ledger.read()
    assert entries[0] == first and len(entries) == 2
    assert entries[0].media[0].sha256 == hashlib.sha256(b"jpeg").hexdigest()
    assert not hasattr(ledger, "remove") and not hasattr(ledger, "write")


def test_paired_bootstrap():
    base = {f"s{i}": ["avoided", "failed", "void"] for i in range(10)}
    cand = {f"s{i}": ["avoided", "avoided", "unresolved"] for i in range(10)}
    out = paired_bootstrap(base, cand, resamples=500, seed=3)
    assert out == paired_bootstrap(base, cand, resamples=500, seed=3)  # seeded
    # base: 1/2 avoided (void excluded); cand: 2/3 (unresolved counts as not avoided); identical sessions -> zero-width CI
    assert out["difference"] == pytest.approx(2 / 3 - 1 / 2)
    assert out["ci95"] == pytest.approx((1 / 6, 1 / 6))
    assert out["void_rate"] == {"baseline": pytest.approx(1 / 3), "candidate": 0.0}
    mixed = paired_bootstrap({"a": ["failed"], "b": ["avoided"]}, {"a": ["avoided"], "b": ["avoided"]}, seed=1)
    assert mixed["difference"] == .5 and mixed["ci95"] == (0.0, 1.0)
    with pytest.raises(ValueError):
        paired_bootstrap({"a": ["avoided"]}, {"b": ["avoided"]})


def test_one_rerun_allowed_only_after_an_outage(tmp_path):
    from backend.evaluation.breakpoints import Sweep, SweepLog, SettingResult, find_breakpoint
    s = Sweep(sweep_id="rerun", dimension="visual_degradation", settings=[0.0], baseline={},
              criterion={"metric": "observation_quality", "comparator": ">=", "threshold": .8})
    log = SweepLog(tmp_path / "s.jsonl")
    sha = log.freeze(s)
    log.record(SettingResult(sweep_sha256=sha, setting=0.0, provider_outage=True))
    log.record(SettingResult(sweep_sha256=sha, setting=0.0, observation_quality=.9))  # rerun after outage
    assert find_breakpoint(s, log.results()).last_pass == 0.0
    with pytest.raises(ValueError, match="no reruns"):  # no second rerun, and never after a real result
        log.record(SettingResult(sweep_sha256=sha, setting=0.0, observation_quality=.95))
