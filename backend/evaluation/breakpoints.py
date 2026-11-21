"""Predeclared one-dimension stress sweeps, breakpoint finder, failure ledger and paired bootstrap (#14).

A sweep is frozen (canonical hash) into the first line of its results file before any
result exists; every later line is one setting's result against that hash. One result
per setting, so a setting cannot be rerun until it passes.
"""
from __future__ import annotations

import json
import operator
import os
from pathlib import Path
import random
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.evaluation.manifest import Outcome, Sha256
from backend.gateway.contracts import Identifier, canonical, digest

Dimension = Literal["input_length_s", "visual_degradation", "style", "job_load", "world_duration_s"]
Metric = Literal["observation_quality", "false_acceptance", "drift", "control_age_s", "coverage",
                 "progress", "latency_ms", "cost_usd"]
Setting = float | str
COMPARE = {"<=": operator.le, "<": operator.lt, ">=": operator.ge, ">": operator.gt}


class PassCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    metric: Metric
    comparator: Literal["<=", "<", ">=", ">"]
    threshold: float


class Sweep(BaseModel):
    """Vary `dimension` over `settings` in order; everything else is the fixed `baseline`."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sweep_id: Identifier
    dimension: Dimension
    settings: tuple[Setting, ...] = Field(min_length=1)
    baseline: dict[str, Any]
    criterion: PassCriterion

    @model_validator(mode="after")
    def one_dimension(self) -> Sweep:
        if self.dimension in self.baseline:
            raise ValueError(f"{self.dimension} is the swept dimension; it cannot also be fixed in baseline")
        if len(set(self.settings)) != len(self.settings):
            raise ValueError("duplicate settings")
        return self

    @property
    def sha256(self) -> str:
        return digest(canonical(self.model_dump(mode="json")))


class SettingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sweep_sha256: Sha256
    setting: Setting
    observation_quality: float | None = None
    false_acceptance: float | None = None  # rate of unsafe acceptances; any > 0 fails the setting
    drift: float | None = None
    control_age_s: float | None = None
    coverage: float | None = None
    progress: float | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None
    counts: dict[Outcome, int] = {}  # attempts by manifest.Outcome
    provider_outage: bool = False  # flagged unrelated API outage


MAX_OUTAGE_RERUNS = 1


class SweepLog:
    """One JSON-lines file: line 1 is the frozen sweep, then append-only results."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def freeze(self, sweep: Sweep) -> str:
        with self.path.open("xb") as handle:  # FileExistsError: a sweep is frozen once
            handle.write(canonical({"sha256": sweep.sha256, "sweep": sweep.model_dump(mode="json")}) + b"\n")
        return sweep.sha256

    def _lines(self) -> list[bytes]:
        return [line for line in self.path.read_bytes().splitlines() if line]

    def sweep(self, expected_sha256: str | None = None) -> Sweep:
        header = json.loads(self._lines()[0])
        sweep = Sweep.model_validate(header["sweep"])
        if sweep.sha256 != header["sha256"] or (expected_sha256 and sweep.sha256 != expected_sha256):
            raise ValueError("sweep changed after freezing")
        return sweep

    def results(self) -> list[SettingResult]:
        return [SettingResult.model_validate_json(line) for line in self._lines()[1:]]

    def record(self, result: SettingResult) -> None:
        sweep = self.sweep()
        if result.sweep_sha256 != sweep.sha256:
            raise ValueError("result was recorded against a different sweep hash")
        if result.setting not in sweep.settings:
            raise ValueError(f"setting {result.setting!r} was not predeclared")
        earlier = [r for r in self.results() if r.setting == result.setting]
        # Only an outage may be rerun, once: a rerun after a real pass/fail would be retry-until-pass.
        if earlier and (len(earlier) > MAX_OUTAGE_RERUNS
                        or any(classify(sweep, r)[1] != "provider_outage" for r in earlier)):
            raise ValueError(f"setting {result.setting!r} already has a result; no reruns except one after an outage")
        _append(self.path, result)


def _append(path: Path, model: BaseModel) -> None:
    with path.open("ab") as handle:
        handle.write(canonical(model.model_dump(mode="json")) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


# --- classification and breakpoint ------------------------------------------------

Status = Literal["pass", "fail", "inconclusive"]


def classify(sweep: Sweep, result: SettingResult) -> tuple[Status, str]:
    """Outage/error -> inconclusive; unsafe false acceptance and abstention at an information limit are distinct failures."""
    if result.sweep_sha256 != sweep.sha256:
        raise ValueError("result belongs to a different sweep")
    if result.provider_outage or result.counts.get(Outcome.ERROR, 0):
        return "inconclusive", "provider_outage"  # an API outage is not the multimodal breakpoint
    if result.false_acceptance:
        return "fail", "unsafe_false_acceptance"
    criterion = sweep.criterion
    value = getattr(result, criterion.metric)
    if value is None:
        return "inconclusive", f"{criterion.metric}_not_measured"
    if COMPARE[criterion.comparator](value, criterion.threshold):
        return "pass", "criterion_met"
    if result.counts.get(Outcome.ABSTAINED, 0):
        return "fail", "abstained_at_information_limit"  # appropriate, but still the measured limit
    return "fail", f"{criterion.metric}_{value}_not_{criterion.comparator}_{criterion.threshold}"


class Breakpoint(BaseModel):
    status: Literal["found", "incomplete_within_tested_range"]
    last_pass: Setting | None
    first_fail: Setting | None
    reason: str
    inconclusive: list[Setting]  # neither pass nor fail
    untested: list[Setting]


def find_breakpoint(sweep: Sweep, results: list[SettingResult]) -> Breakpoint:
    by_setting = {r.setting: r for r in results}
    last_pass, inconclusive, untested = None, [], []
    for setting in sweep.settings:
        if setting not in by_setting:
            untested.append(setting)
            continue
        status, reason = classify(sweep, by_setting[setting])
        if status == "fail":
            return Breakpoint(status="found", last_pass=last_pass, first_fail=setting, reason=reason,
                              inconclusive=inconclusive, untested=untested)
        if status == "pass":
            last_pass = setting
        else:
            inconclusive.append(setting)
    return Breakpoint(status="incomplete_within_tested_range", last_pass=last_pass, first_fail=None,
                      reason="no failing setting within the tested range", inconclusive=inconclusive, untested=untested)


# --- failure ledger -------------------------------------------------------------------

class MediaRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    sha256: Sha256


def media_ref(path: str | Path) -> MediaRef:
    return MediaRef(path=str(path), sha256=digest(Path(path).read_bytes()))


class FailureEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sweep_sha256: Sha256
    setting: Setting
    reason: str
    metrics: dict[str, float | None]  # the failing metric values
    media: tuple[MediaRef, ...] = ()
    change: str | None = None  # exact repair applied, if any
    remaining_limits: str


class FailureLedger:
    """Append-only JSON lines; entries are never edited or removed."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, entry: FailureEntry) -> None:
        _append(self.path, entry)

    def read(self) -> list[FailureEntry]:
        if not self.path.exists():
            return []
        return [FailureEntry.model_validate_json(line) for line in self.path.read_bytes().splitlines() if line]


# --- session-level paired bootstrap -------------------------------------------------------

AttackOutcome = Literal["avoided", "failed", "unresolved", "void"]


def _rate(sessions: list[list[str]]) -> float | None:
    scored = [o for s in sessions for o in s if o != "void"]
    return sum(o == "avoided" for o in scored) / len(scored) if scored else None


def paired_bootstrap(baseline: dict[str, list[AttackOutcome]], candidate: dict[str, list[AttackOutcome]],
                     *, resamples: int = 10_000, seed: int = 0) -> dict:
    """Candidate minus baseline avoidance rate, resampling paired sessions (the independent unit).

    Keys pair sessions run from the same frozen scripts and references. Like `attacks.aggregate`,
    unresolved counts as not avoided and voids are excluded; void rates are reported per system.
    """
    if baseline.keys() != candidate.keys() or not baseline:
        raise ValueError("need the same non-empty set of paired sessions for both systems")
    keys = sorted(baseline)
    pairs = [(baseline[k], candidate[k]) for k in keys]

    def diff(sample):
        a, b = _rate([p[0] for p in sample]), _rate([p[1] for p in sample])
        return None if a is None or b is None else b - a

    rng = random.Random(seed)
    draws = sorted(d for d in (diff(rng.choices(pairs, k=len(pairs))) for _ in range(resamples)) if d is not None)
    point = diff(pairs)

    def void_rate(system):
        outcomes = [o for s in system.values() for o in s]
        return sum(o == "void" for o in outcomes) / len(outcomes) if outcomes else None

    return {
        "sessions": len(keys),
        "difference": point,
        "ci95": (draws[int(.025 * (len(draws) - 1))], draws[int(.975 * (len(draws) - 1))]) if draws else None,
        "resamples_used": len(draws),  # resamples where either system had no scored attack are dropped
        "seed": seed,
        "void_rate": {"baseline": void_rate(baseline), "candidate": void_rate(candidate)},
    }
