"""Locked real-camera evaluation set, holdout guard, results ledger and report (#13).

The manifest core (episodes, prompts, thresholds, frozen #24 attack suite) is
hashed with gateway `canonical`/`digest`. The post-freeze, judge-selected fresh
capture is appended as a labelled addition outside that hash; `full_sha256`
covers core plus additions and is pinned by the first FinalRun.
"""
from __future__ import annotations

from collections import Counter
from enum import StrEnum
import json
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.evaluation.common import wilson_interval
from backend.gateway.contracts import MODELS, ErrorCode, Identifier, Service, canonical, digest

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MAX_EPISODE_SECONDS = 120
TARGET = {"dev": 4, "test": 8, "cameras": 3, "windows": 2}
FIXTURE_PROVIDERS = {"demoscenarioprovider", "fixture"}
FRESH_CAPTURE_LABEL = "post_freeze_judge_selected_fresh_capture"


class HoldoutError(PermissionError):
    """Test-split bytes requested outside a verified final run."""


class Episode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    episode_id: Identifier
    source_id: Identifier  # camera
    provider: str  # where the bytes came from, e.g. "nyc_dot"
    modality: Literal["continuous", "snapshot"]
    source_hashes: tuple[Sha256, ...] = Field(min_length=1)
    window_id: Identifier  # acquisition window
    duration_s: float = Field(ge=0, le=MAX_EPISODE_SECONDS)
    split: Literal["dev", "test"]
    parent_id: Identifier | None = None  # derivative of another episode

    @field_validator("provider")
    @classmethod
    def external_only(cls, value: str) -> str:
        if value.lower() in FIXTURE_PROVIDERS:
            raise ValueError("fixture/DemoScenarioProvider sources are excluded from the external benchmark")
        return value


def check_lineage(episodes: tuple[Episode, ...]) -> None:
    """Unique ids; no content hash in both splits; derivatives name a known parent and inherit its split."""
    by_id: dict[str, Episode] = {}
    split_of: dict[str, str] = {}
    for episode in episodes:
        if episode.episode_id in by_id:
            raise ValueError(f"duplicate episode {episode.episode_id}")
        by_id[episode.episode_id] = episode
        for h in episode.source_hashes:
            if split_of.setdefault(h, episode.split) != episode.split:
                raise ValueError(f"{episode.episode_id}: source hash {h} appears in both dev and test")
    for episode in episodes:
        if episode.parent_id is None:
            continue
        parent = by_id.get(episode.parent_id)
        if parent is None:
            raise ValueError(f"{episode.episode_id}: unknown parent {episode.parent_id}")
        if parent.split != episode.split:
            raise ValueError(f"{episode.episode_id}: split {episode.split} differs from parent split {parent.split}")


def split_report(episodes: tuple[Episode, ...] | list[Episode]) -> dict:
    """Actual size and coverage against the #13 target; shortfalls are reported, not raised."""
    dev = [e for e in episodes if e.split == "dev"]
    test = [e for e in episodes if e.split == "test"]
    cameras = {e.source_id for e in episodes}
    windows = {e.window_id for e in episodes}
    unseen = sorted({e.source_id for e in test} - {e.source_id for e in dev})
    report = {"dev": len(dev), "test": len(test), "cameras": len(cameras), "windows": len(windows),
              "test_cameras_absent_from_dev": unseen,
              "modalities": dict(Counter(e.modality for e in episodes))}
    shortfalls = [f"{key}: {report[key]} < {want}" for key, want in TARGET.items() if report[key] < want]
    if not unseen:
        shortfalls.append("no test camera absent from development")
    report["shortfalls"] = shortfalls
    return report


class Manifest(BaseModel):
    """Everything frozen before final runs; its canonical hash is the lock."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    episodes: tuple[Episode, ...]
    prompts: dict[Identifier, Sha256]  # prompt id -> prompt text hash
    thresholds: dict[Identifier, float]
    attack_suite_sha256: Sha256  # opaque frozen #24 suite hash

    @model_validator(mode="after")
    def lineage(self) -> Manifest:
        check_lineage(self.episodes)
        return self

    @property
    def sha256(self) -> str:
        return digest(canonical(self.model_dump(mode="json")))


class Addition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    label: Literal["post_freeze_judge_selected_fresh_capture"] = FRESH_CAPTURE_LABEL
    episode: Episode


class LockedManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sha256: Sha256
    manifest: Manifest
    additions: tuple[Addition, ...] = ()

    def episodes(self) -> tuple[Episode, ...]:
        return self.manifest.episodes + tuple(a.episode for a in self.additions)

    @property
    def full_sha256(self) -> str:
        """Core hash plus additions: any swapped, removed or appended addition changes it."""
        return digest(canonical(self.model_dump(mode="json")))


def opened_marker(path: str | Path) -> Path:
    """Exists once a FinalRun has opened the holdout; holds the full hash it pinned."""
    return Path(str(path) + ".opened")


def _write(path: Path, locked: LockedManifest) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical(locked.model_dump(mode="json")))
    os.replace(temporary, path)


def lock(manifest: Manifest, path: str | Path) -> str:
    """Write the locked manifest once; returns the hash to publish/pin for final runs."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} is already locked")
    _write(path, LockedManifest(sha256=manifest.sha256, manifest=manifest))
    return manifest.sha256


def verify_lock(path: str | Path, expected_sha256: str | None = None) -> LockedManifest:
    """Reject any edit to the frozen core. Pass the published hash to also catch a re-hashed file."""
    locked = LockedManifest.model_validate_json(Path(path).read_bytes())
    actual = locked.manifest.sha256
    if actual != locked.sha256 or (expected_sha256 is not None and actual != expected_sha256):
        raise ValueError("manifest changed after locking")
    check_lineage(locked.episodes())
    return locked


def add_fresh_capture(path: str | Path, episode: Episode, expected_sha256: str) -> LockedManifest:
    """Append the single judge-selected post-freeze capture, only before any FinalRun, without touching the core hash."""
    if opened_marker(path).exists():
        raise HoldoutError("holdout already opened: no additions after a FinalRun")
    locked = verify_lock(path, expected_sha256)
    if episode.split != "test":
        raise ValueError("the post-freeze fresh capture is a test episode")
    if locked.additions:
        raise ValueError("at most one post-freeze fresh capture")
    updated = locked.model_copy(update={"additions": locked.additions + (Addition(episode=episode),)})
    check_lineage(updated.episodes())
    _write(Path(path), updated)
    return updated


class FinalRun:
    """The only holder of test-split access; exists only for a verified, pinned lock.

    The first FinalRun writes `<lock>.opened` with the full hash; later runs and `verify()`
    must match it, so additions cannot change once the holdout has been opened.
    """

    def __init__(self, path: str | Path, expected_sha256: str):
        self.path = Path(path)
        self.locked = verify_lock(path, expected_sha256)
        self.full_sha256 = self.locked.full_sha256
        marker = opened_marker(path)
        try:
            with marker.open("x") as handle:
                handle.write(self.full_sha256)
        except FileExistsError:
            pass
        self.verify()

    def verify(self) -> LockedManifest:
        """Re-read the lock; fail if core or additions differ from what was pinned at first opening."""
        locked = verify_lock(self.path, self.locked.sha256)
        pinned = opened_marker(self.path).read_text().strip()
        if locked.full_sha256 != pinned or self.full_sha256 != pinned:
            raise ValueError("manifest additions changed after the holdout was opened")
        return locked


def read_source(path: str | Path, manifest: Manifest | LockedManifest,
                run: FinalRun | None = None) -> tuple[Episode, bytes]:
    """Load episode bytes by content hash; test bytes need a FinalRun.

    Without `run` the caller's in-memory manifest is trusted as given: this is a tripwire
    against accidentally loading holdout bytes, not a security boundary. A FinalRun (which
    re-verifies the lock on disk) is the real guard.
    """
    data = Path(path).read_bytes()
    fingerprint = digest(data)
    episodes = run.locked.episodes() if run else (
        manifest.episodes() if isinstance(manifest, LockedManifest) else manifest.episodes)
    episode = next((e for e in episodes if fingerprint in e.source_hashes), None)
    if episode is None:
        raise ValueError("bytes are not part of the manifest")
    if episode.split == "test" and run is None:
        raise HoldoutError(f"{episode.episode_id} is held out; load it through FinalRun")
    return episode, data


class Outcome(StrEnum):
    SUCCESS = "success"
    INVALID_OUTPUT = "invalid_output"
    REFUSAL = "refusal"
    ERROR = "error"
    GATE_REJECTED = "gate_rejected"
    RETRY = "retry"
    ABSTAINED = "abstained"
    VOID = "void"


class Attempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    episode_id: Identifier
    system: Identifier  # "full", "baseline", or a defender variant id
    outcome: Outcome
    cost_usd: float = Field(ge=0)
    latency_ms: float = Field(ge=0)
    stochastic: bool = False  # independent Genie trial
    manifest_sha256: Sha256 | None = None
    error_code: ErrorCode | None = None  # required for RETRY: the gateway failure being retried

    @model_validator(mode="after")
    def retry_has_error(self) -> Attempt:
        if self.outcome == Outcome.RETRY and self.error_code is None:
            raise ValueError("a retry must carry the gateway error_code that caused it")
        return self


class ResultsLedger:
    """Append-only JSON lines; every attempt is kept, failures included. `model` sets the row type."""

    def __init__(self, path: str | Path, model: type[BaseModel] = Attempt):
        self.path = Path(path)
        self.model = model

    def append(self, attempt: BaseModel) -> None:
        with self.path.open("ab") as handle:
            handle.write(canonical(attempt.model_dump(mode="json")) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    def read(self) -> list:
        if not self.path.exists():
            return []
        return [self.model.model_validate_json(line) for line in self.path.read_bytes().splitlines() if line]


def report(attempts: list[Attempt]) -> dict:
    """Per system: count of every category, success rate over non-void, non-retry attempts with Wilson 95% CI."""
    out = {}
    for system in sorted({a.system for a in attempts}):
        rows = [a for a in attempts if a.system == system]
        counts = Counter(a.outcome for a in rows)
        # Void and retry are reported but not scored: a retry is followed by the attempt that counts.
        eligible = len(rows) - counts[Outcome.VOID] - counts[Outcome.RETRY]
        successes = counts[Outcome.SUCCESS]
        stochastic = any(a.stochastic for a in rows)
        out[system] = {
            "attempts": len(rows),
            "counts": {o.value: counts[o] for o in Outcome},
            "eligible": eligible,
            "success_rate": successes / eligible if eligible else None,
            "ci95": wilson_interval(successes, eligible),
            "cost_usd": sum(a.cost_usd for a in rows),
            "latency_ms": sum(a.latency_ms for a in rows),
            "trials": ("independent stochastic Genie trials: same scripts and references, not the same worlds"
                       if stochastic else "deterministic replay of recorded inputs"),
        }
    return out


class BaselineDefinition(BaseModel):
    """Single-call Hydrogen evidence/edit baseline (#13). Calls are implemented with #5/#6."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    baseline_id: Identifier = "hydrogen_single_call"
    model_id: Literal["gemini-4-hydrogen"] = MODELS[Service.HYDROGEN]
    calls: Literal[1] = 1
    prompt_id: Identifier
    # Fairness: identical raw input, output schema and calibration to the full pipeline.
    input_sha256: Sha256
    schema_sha256: Sha256
    calibration_sha256: Sha256
    # Full pipeline minus baseline, filled from measured runs; None until measured.
    added_calls: int | None = None
    added_time_ms: float | None = None
    added_cost_usd: float | None = None
