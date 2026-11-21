"""Bounded Carbon repair of the defender policy, tested on held-out cameras (#14).

Carbon sees development failure logs only (never adversary prompts, steering
text or the frozen test suite), may change only declared surface fields within
bounds, gets at most `max_rounds` proposals, and tuning stops the moment the
holdout is opened. Every proposal, rejection and regression is kept.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.gateway.contracts import CallContext, GatewayError, Identifier, Service, canonical, digest
from .attacks import AttackRecord, AttackScore, AttackScript, aggregate, assert_no_leakage, load_suite, suite_hash
from .manifest import Episode, FinalRun, HoldoutError, LockedManifest, Sha256, opened_marker, plan_path, verify_lock

Value = Union[float, str]


# --- repair surface ---------------------------------------------------------------

class NumberField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["number"] = "number"
    min: float
    max: float


class ChoiceField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["choice"] = "choice"
    options: tuple[str, ...] = Field(min_length=1)


class TextField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["text"] = "text"
    max_length: int = Field(gt=0)


FieldSpec = Annotated[NumberField | ChoiceField | TextField, Field(discriminator="kind")]


class RepairSurface(BaseModel):
    """The only defender-policy fields a repair may edit. Anything undeclared is out of surface."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    fields: dict[Identifier, FieldSpec]

    def check(self, name: str, value) -> None:
        spec = self.fields.get(name)
        if spec is None:
            raise ValueError(f"{name} is outside the repair surface")
        if isinstance(spec, NumberField):
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and spec.min <= value <= spec.max
        elif isinstance(spec, ChoiceField):
            ok = value in spec.options
        else:
            ok = isinstance(value, str) and len(value) <= spec.max_length
        if not ok:
            raise ValueError(f"{name}={value!r} is out of bounds")

    def validate_values(self, values: dict[str, Value]) -> None:
        if set(values) != set(self.fields):
            raise ValueError("values must cover exactly the declared surface")
        for name, value in values.items():
            self.check(name, value)


class FieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    value: Value


class RepairProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: list[FieldChange] = Field(min_length=1)
    rationale: str = Field(max_length=2000)


# --- comparison plan ----------------------------------------------------------------

class PlanSlot(BaseModel):
    """One planned comparison: a script on an episode, replicate `replicate`, run before and after repair."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    split: Literal["dev", "test"]
    episode_id: Identifier
    script: AttackScript
    replicate: int = Field(ge=0)

    @property
    def comparison_id(self) -> str:
        """Derived from the slot, so a run cannot be relabelled to pair with another."""
        script_sha = digest(canonical(self.script.model_dump(mode="json")))
        return digest(canonical([self.episode_id, script_sha, self.replicate]))[:16]

    def __str__(self) -> str:
        return f"{self.split}:{self.episode_id}/{self.script.attack_type.value}/r{self.replicate}/{self.comparison_id}"


class ComparisonPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    manifest_sha256: Sha256
    slots: tuple[PlanSlot, ...] = Field(min_length=1)


def _validate_plan_slots(locked: LockedManifest, suite: list[AttackScript],
                         slots: list[PlanSlot] | tuple[PlanSlot, ...]) -> None:
    """Validate the roster against the actual manifest, including its fresh capture."""
    split_of = {e.episode_id: e.split for e in locked.episodes()}
    for slot in slots:
        if split_of.get(slot.episode_id) != slot.split:
            raise ValueError(f"{slot}: not a {slot.split}-split episode of the locked manifest")
        if slot.split == "test" and slot.script not in suite:
            raise ValueError(f"{slot}: test slots must use frozen-suite scripts")
    if len(set(slots)) != len(slots):
        raise ValueError("duplicate plan slots")
    dev_cameras = {e.source_id for e in locked.episodes() if e.split == "dev"}
    held_out = [e.episode_id for e in locked.episodes() if e.split == "test" and e.source_id not in dev_cameras]
    test_slots = {s for s in slots if s.split == "test"}
    k = max((s.replicate for s in test_slots), default=0) + 1
    required = {PlanSlot(split="test", episode_id=e, script=script, replicate=r)
                for e in held_out for script in suite for r in range(k)}
    missing, extra = required - test_slots, test_slots - required
    if missing or extra:
        names = lambda group: ", ".join(sorted(map(str, group))) or "none"
        raise ValueError(f"test roster must be frozen suite x held-out episodes x replicates 0..{k - 1}; "
                         f"missing: {names(missing)}; extra: {names(extra)}")


def register_plan(lock_path: str | Path, pinned_sha256: str, suite_path: str | Path, slots: list[PlanSlot]) -> str:
    """Write the comparison roster once, before any FinalRun opens the holdout; returns its hash.

    The test roster must be complete: every frozen-suite script on every test episode whose camera
    is absent from development, for replicates 0..k-1 with one k throughout. The dev roster is free.
    Order: add the fresh capture (if any) first, then register the plan, then open a FinalRun.
    """
    if opened_marker(lock_path).exists():
        raise HoldoutError("holdout already opened: the comparison plan must be registered before any FinalRun")
    locked = verify_lock(lock_path, pinned_sha256)
    suite = load_suite(suite_path, locked.manifest.attack_suite_sha256)
    _validate_plan_slots(locked, suite, slots)
    data = canonical(ComparisonPlan(manifest_sha256=locked.sha256, slots=tuple(slots)).model_dump(mode="json"))
    with plan_path(lock_path).open("xb") as handle:
        handle.write(data)
    return digest(data)


def read_plan(lock_path: str | Path, expected_sha256: str | None = None) -> tuple[ComparisonPlan, str]:
    data = plan_path(lock_path).read_bytes()
    plan = ComparisonPlan.model_validate_json(data)
    sha = digest(canonical(plan.model_dump(mode="json")))
    if sha != digest(data) or (expected_sha256 is not None and sha != expected_sha256):
        raise ValueError("comparison plan changed after it was pinned")
    return plan, sha


# --- evidence packet ----------------------------------------------------------------

class ScoredEpisode(BaseModel):
    """One scored attack on a manifest episode, plus the defender's own action log."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    episode: Episode
    replicate: int = Field(ge=0)  # planned replicate index, shared across variants; never the runtime session
    score: AttackScore
    defender_actions: tuple[str, ...] = ()

    @property
    def slot(self) -> PlanSlot:
        return PlanSlot(split=self.episode.split, episode_id=self.episode.episode_id,
                        script=self.score.record.script, replicate=self.replicate)

    @property
    def comparison_id(self) -> str:
        return self.slot.comparison_id


def build_packet(failures: list[ScoredEpisode], test_suite: list[AttackScript]) -> list[dict]:
    """Development failure logs for Carbon. Test-split or frozen-suite input raises HoldoutError."""
    packet = []
    for f in failures:
        if f.episode.split != "dev" or f.score.record.script in test_suite:
            raise HoldoutError(f"{f.episode.episode_id}: repair evidence must come from development only")
        s = f.score
        # Only what the defender could see: the hazard type once it was visible (never the scripted type of an
        # unseen attack). No window_end or anything derived from it: window_end is send_frame + window, and
        # together with first_visible_frame any duration based on it gives the send time back.
        seen = s.first_visible_frame is not None
        packet.append({"episode_id": f.episode.episode_id, "hazard": s.record.script.attack_type.value if seen else None,
                       "outcome": s.outcome, "void_reason": s.void_reason,
                       "first_visible_frame": s.first_visible_frame, "contact_frame": s.contact_frame,
                       "cleared_frame": s.cleared_frame, "lead_frames": s.lead_frames,
                       "required_lead_frames": s.required_lead_frames,
                       "defender_actions": list(f.defender_actions)})
    check_packet(packet, [f.score.record for f in failures], test_suite)
    return packet


def check_packet(payload, dev_records: list[AttackRecord], test_suite: list[AttackScript]) -> None:
    """No adversary fields, no steering text (dev or test), no frozen-suite hash."""
    held = [AttackRecord(script=s, session_id="holdout", send_frame=s.trigger_frame, source="frozen") for s in test_suite]
    assert_no_leakage(payload, dev_records + held)
    if test_suite and suite_hash(test_suite) in json.dumps(payload):
        raise ValueError("frozen suite hash in repair payload")


# --- bounded session and sealed trial ----------------------------------------------

class ProposalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    round: int
    request_id: str | None
    output: dict | None  # raw Carbon output, kept even when rejected
    accepted: bool
    reason: str | None = None


class RepairTrial(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    surface: RepairSurface
    max_rounds: int
    proposals: tuple[ProposalRecord, ...]
    diff: dict[str, tuple[Value, Value]]  # field -> (before, after); empty if nothing was accepted
    dev_before: dict
    dev_after: dict
    holdout_before: dict
    holdout_after: dict
    dev_delta: dict[str, float | None]  # avoidance_rate after - before, per hazard type and overall
    holdout_delta: dict[str, float | None]
    regressions: dict[Literal["dev", "holdout"], list[str]]  # hazard types that got worse
    unsuccessful_fixes: list[str]  # targeted hazard types that did not improve on holdout
    manifest_sha256: str
    manifest_full_sha256: str  # core + additions, as pinned by the FinalRun
    suite_sha256: str


def delta(before: dict, after: dict) -> dict[str, float | None]:
    out = {}
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key, {}).get("avoidance_rate"), after.get(key, {}).get("avoidance_rate")
        out[key] = None if a is None or b is None else a - b
    return out


class RepairSession:
    """At most `max_rounds` Carbon proposals; none once any FinalRun has opened this manifest's holdout."""

    def __init__(self, surface: RepairSurface, values: dict[str, Value], failures: list[ScoredEpisode],
                 test_suite: list[AttackScript], *, lock_path: str | Path, max_rounds: int = 2):
        surface.validate_values(values)
        self.surface, self.initial, self.values = surface, dict(values), dict(values)
        self.failures, self.test_suite = failures, test_suite
        self.packet = build_packet(failures, test_suite)
        self.lock_path, self.run, self.sealed, self.plan_sha256 = Path(lock_path), None, False, None
        self.max_rounds, self.proposals, self.rounds = max_rounds, [], 0  # rounds: reserved before the Carbon await

    def open_holdout(self, lock_path: str | Path, pinned_sha256: str) -> FinalRun:
        """The session's only way to the holdout; tuning ends here. Needs a registered plan, whose hash it pins."""
        if Path(lock_path) != self.lock_path:
            raise ValueError("holdout must be opened on this session's manifest")
        if self.run is None:
            if not plan_path(lock_path).exists():
                raise HoldoutError("register the comparison plan before opening the holdout")
            run = FinalRun(lock_path, pinned_sha256)  # verify(): the plan must be the one committed at first opening
            plan, self.plan_sha256 = read_plan(lock_path, run.plan_sha256)
            if plan.manifest_sha256 != pinned_sha256:
                raise ValueError("comparison plan was registered for a different manifest")
            self.run = run
        return self.run

    def holdout_opened(self) -> bool:
        return self.run is not None or opened_marker(self.lock_path).exists()

    async def propose(self, gateway, context: CallContext) -> ProposalRecord:
        if self.holdout_opened():
            raise HoldoutError("no tuning after the holdout is opened")
        if self.rounds >= self.max_rounds:
            raise RuntimeError(f"repair attempt cap of {self.max_rounds} reached")
        body = {"surface": self.surface.model_dump(mode="json"), "values": self.values, "failures": self.packet}
        check_packet(body, [f.score.record for f in self.failures], self.test_suite)
        prompt = ("You repair a driving defender policy. Below are its development failures and its editable "
                  "parameters with bounds. Propose changes to declared fields only, within bounds.\n" + json.dumps(body))
        self.rounds += 1
        round_ = self.rounds
        request_id = output = None
        try:
            result = await gateway.generate(Service.CARBON, stage="repair", context=context,
                                            input_value=[{"type": "text", "text": prompt}], response_model=RepairProposal)
            request_id, output = result.request_id, result.output
            proposal = RepairProposal.model_validate(output)
            updated = dict(self.values)
            for change in proposal.changes:
                self.surface.check(change.field, change.value)
                updated[change.field] = change.value
        except (GatewayError, ValidationError, ValueError) as exc:
            request_id = request_id or getattr(exc, "request_id", None)
            record = ProposalRecord(round=round_, request_id=request_id, output=output if isinstance(output, dict) else None,
                                    accepted=False, reason=str(exc)[:500])
        else:
            if self.holdout_opened():  # opened while Carbon was answering
                record = ProposalRecord(round=round_, request_id=request_id, output=output, accepted=False,
                                        reason="holdout opened before the proposal could be applied")
            else:
                self.values = updated
                record = ProposalRecord(round=round_, request_id=request_id, output=output, accepted=True)
        self.proposals.append(record)
        return record

    def seal(self, run: FinalRun, suite_path: str | Path, *, dev_before: list[ScoredEpisode],
             dev_after: list[ScoredEpisode], holdout_before: list[ScoredEpisode],
             holdout_after: list[ScoredEpisode]) -> RepairTrial:
        """Record the dev rerun and the held-out-camera holdout for the run from `open_holdout`.

        Dev and holdout before/after must each cover exactly their split's slots of the plan pinned at
        `open_holdout`. Rows pair by slot (episode, script, replicate); record.session_id still identifies
        the actual, distinct runtime session for each rollout. Within a split, each runtime attack
        (session, script) fills at most one row, and before and after share no session.

        Known limit: swapping two real, distinct rollouts between replicates of the same episode and
        script is not detected; that needs a slot -> session binding recorded at execution (#32).
        """
        if run is None or run is not self.run:
            raise HoldoutError("seal needs the FinalRun returned by this session's open_holdout")
        if self.sealed:
            raise HoldoutError("trial already sealed")
        locked = run.verify()
        plan, _ = read_plan(self.lock_path, self.plan_sha256)
        if plan.manifest_sha256 != locked.sha256:
            raise ValueError("comparison plan was registered for a different manifest")
        suite = load_suite(suite_path, locked.manifest.attack_suite_sha256)
        # A fresh-capture write that began before registration can finish after it. Recheck
        # the roster against the FinalRun's pinned episodes before accepting any results.
        _validate_plan_slots(locked, suite, plan.slots)
        episodes = locked.episodes()
        dev_cameras = {e.source_id for e in episodes if e.split == "dev"}
        for item in holdout_before + holdout_after:
            e = item.episode
            if e not in episodes or e.split != "test" or e.source_id in dev_cameras or item.score.record.script not in suite:
                raise ValueError(f"{e.episode_id}: holdout must be a frozen-suite attack on a held-out test camera")
        for item in dev_before + dev_after:
            if item.episode not in episodes or item.episode.split != "dev":
                raise ValueError(f"{item.episode.episode_id}: dev scores must come from dev-split manifest episodes")
        names = lambda slots: ", ".join(sorted(map(str, slots))) or "none"
        for name, split, side in (("dev before", "dev", dev_before), ("dev after", "dev", dev_after),
                                  ("holdout before", "test", holdout_before), ("holdout after", "test", holdout_after)):
            planned = {s for s in plan.slots if s.split == split}
            got = Counter(i.slot for i in side)
            missing, extra, dupes = planned - set(got), set(got) - planned, [s for s, n in got.items() if n > 1]
            if missing or extra or dupes:
                raise ValueError(f"{name} must cover exactly the planned slots; missing: {names(missing)}; "
                                 f"extra: {names(extra)}; duplicate: {names(dupes)}")
        for name, before, after in (("dev", dev_before, dev_after), ("holdout", holdout_before, holdout_after)):
            for side, rows in (("before", before), ("after", after)):
                attacks = Counter((i.score.record.session_id, i.score.record.script) for i in rows)
                if any(n > 1 for n in attacks.values()):
                    raise ValueError(f"{name} {side} reuses one runtime attack for several slots")
            shared = {i.score.record.session_id for i in before} & {i.score.record.session_id for i in after}
            if shared:
                raise ValueError(f"{name} before and after reuse runtime sessions: {', '.join(sorted(shared))}")
        self.sealed = True
        hb, ha = aggregate([i.score for i in holdout_before]), aggregate([i.score for i in holdout_after])
        db, da = aggregate([i.score for i in dev_before]), aggregate([i.score for i in dev_after])
        dev_delta, holdout_delta = delta(db, da), delta(hb, ha)
        targeted = sorted({f.score.record.script.attack_type.value for f in self.failures})
        return RepairTrial(
            surface=self.surface, max_rounds=self.max_rounds, proposals=tuple(self.proposals),
            diff={k: (self.initial[k], v) for k, v in self.values.items() if v != self.initial[k]},
            dev_before=db, dev_after=da, holdout_before=hb, holdout_after=ha,
            dev_delta=dev_delta, holdout_delta=holdout_delta,
            regressions={"dev": [k for k, d in dev_delta.items() if d is not None and d < 0],
                         "holdout": [k for k, d in holdout_delta.items() if d is not None and d < 0]},
            unsuccessful_fixes=[k for k in targeted if (holdout_delta.get(k) or 0) <= 0],
            manifest_sha256=locked.sha256, manifest_full_sha256=locked.full_sha256, suite_sha256=suite_hash(suite))
