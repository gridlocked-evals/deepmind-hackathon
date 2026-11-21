import asyncio
import json

import pytest

from backend.evaluation.attacks import AttackRecord, AttackScore, AttackScript, AttackType, save_suite
from backend.evaluation.manifest import Episode, FinalRun, HoldoutError, Manifest, ResultsLedger, lock
from backend.evaluation.repair import (ChoiceField, NumberField, RepairSession, RepairSurface, RepairTrial,
                                       ScoredEpisode, TextField, build_packet)
from backend.gateway.contracts import CallContext, CallResult, digest

CONTEXT = CallContext(episode_id="d1", prompt_id="repair-v1")
SURFACE = RepairSurface(fields={"brake_lead_s": NumberField(min=.5, max=3), "mode": ChoiceField(options=("calm", "alert")),
                                "hazard_note": TextField(max_length=40)})
VALUES = {"brake_lead_s": 1.0, "mode": "calm", "hazard_note": ""}
PED, TRUCK = AttackType.PEDESTRIAN_STEPS_OUT, AttackType.DOUBLE_PARKED_TRUCK
DEV_TEXT, TEST_TEXT = "A pedestrian darts out from behind the van.", "A truck sits double-parked across the lane."
TEST_SUITE = [AttackScript(attack_type=TRUCK, steering_text=TEST_TEXT, trigger_frame=48),
              AttackScript(attack_type=PED, steering_text="A pedestrian steps out.", trigger_frame=300)]


def ep(episode_id, split="dev", camera="cam1"):
    return Episode(episode_id=episode_id, source_id=camera, provider="nyc_dot", modality="continuous",
                   source_hashes=(digest(episode_id.encode()),),
                   window_id="w1", duration_s=60, split=split)


def scored(episode, script, outcome, actions=(), *, comparison_id="replicate-1", session_id=None):
    record = AttackRecord(script=script, session_id=session_id or episode.episode_id, send_frame=script.trigger_frame,
                          source="adaptive" if episode.split == "dev" else "frozen")
    score = AttackScore(record=record, outcome=outcome, first_visible_frame=60, contact_frame=80 if outcome == "failed" else None,
                        lead_frames=20, required_lead_frames=36, sample_every=6, window_end=300)
    return ScoredEpisode(episode=episode, comparison_id=comparison_id, score=score, defender_actions=actions)


DEV = ep("d1")
DEV_FAIL = scored(DEV, AttackScript(attack_type=PED, steering_text=DEV_TEXT, trigger_frame=24), "failed",
                  ("hold_forward", "release_keys"))


class FakeGateway:
    def __init__(self, *outputs):
        self.outputs, self.inputs = list(outputs), []

    async def generate(self, service, *, stage, input_value, context, response_model=None):
        assert stage == "repair"
        self.inputs.append(input_value[0]["text"])
        return CallResult(f"req-{len(self.inputs)}", None, "m", "h", "h", self.outputs.pop(0), None, "reservation_retained")


def change(field, value):
    return {"changes": [{"field": field, "value": value}], "rationale": "brake earlier"}


def session_for(lock_path="never-opened.lock.json"):
    return RepairSession(SURFACE, VALUES, [DEV_FAIL], TEST_SUITE, lock_path=lock_path)


def propose(session, gateway):
    return asyncio.run(session.propose(gateway, CONTEXT))


def test_out_of_surface_and_out_of_bounds_edits_rejected():
    for field, value in [("contact_threshold", .9), ("brake_lead_s", 9.0), ("brake_lead_s", "2"), ("mode", "panic"),
                         ("hazard_note", "x" * 41)]:
        session = session_for()
        record = propose(session, FakeGateway(change(field, value)))
        assert not record.accepted and record.request_id == "req-1" and session.values == VALUES
    session = session_for()
    assert propose(session, FakeGateway(change("brake_lead_s", 2.0))).accepted and session.values["brake_lead_s"] == 2.0


def test_packet_has_no_adversary_or_test_data():
    packet = build_packet([DEV_FAIL], TEST_SUITE)
    assert packet[0]["hazard"] == PED.value and packet[0]["defender_actions"] == ["hold_forward", "release_keys"]
    gateway = FakeGateway(change("mode", "alert"))
    propose(session_for(), gateway)
    for text in (json.dumps(packet), gateway.inputs[0]):
        for secret in (DEV_TEXT, TEST_TEXT, "steering_text", "attack_type", "send_frame", "trigger_frame"):
            assert secret not in text
    leaky = DEV_FAIL.model_copy(update={"defender_actions": ("heard: " + DEV_TEXT,)})
    with pytest.raises(ValueError, match="steering text"):
        build_packet([leaky], TEST_SUITE)


def test_unseen_hazard_and_send_timing_stay_out_of_packet():
    script = AttackScript(attack_type=PED, steering_text=DEV_TEXT, trigger_frame=24)
    record = AttackRecord(script=script, session_id="d1", send_frame=24, source="adaptive")
    unseen = dict(record=record, required_lead_frames=36, sample_every=6, window_end=264)  # 264 = send + window
    never = ScoredEpisode(episode=DEV, comparison_id="never-rendered", score=AttackScore(**unseen, outcome="void", void_reason="never_rendered"))
    paired = ScoredEpisode(episode=DEV, comparison_id="paired-void", score=AttackScore(**unseen, outcome="unresolved",
                                                          paired_void_reason="never_rendered"))
    packet = build_packet([never, paired], TEST_SUITE)  # also runs check_packet
    for row in packet:
        assert row["hazard"] is None
    assert PED.value not in json.dumps(packet) and "264" not in json.dumps(packet)
    seen = build_packet([DEV_FAIL], TEST_SUITE)[0]
    assert seen["hazard"] == PED.value
    # no send-relative field: first_visible_frame plus any window-based duration would rebuild the send time
    for row in packet + [seen]:
        assert not {"window_end", "observed_frames"} & set(row)


def test_test_split_source_raises():
    with pytest.raises(HoldoutError):
        build_packet([scored(ep("t1", "test", "cam9"), TEST_SUITE[0], "failed")], TEST_SUITE)
    with pytest.raises(HoldoutError):  # a frozen-suite script replayed on a dev camera is still test data
        build_packet([scored(DEV, TEST_SUITE[0], "failed")], TEST_SUITE)


def test_attempt_cap_and_every_proposal_recorded():
    session = session_for()
    propose(session, FakeGateway(change("physics_gravity", 1.0)))
    propose(session, FakeGateway(change("brake_lead_s", 2.0)))
    with pytest.raises(RuntimeError, match="cap"):
        propose(session, FakeGateway(change("brake_lead_s", 2.5)))
    assert [(p.round, p.accepted) for p in session.proposals] == [(1, False), (2, True)]


TEST = ep("b1", "test", "cam9")
HOLDOUT_BEFORE = [scored(TEST, TEST_SUITE[0], "avoided"), scored(TEST, TEST_SUITE[1], "failed")]
HOLDOUT_AFTER = [scored(TEST, TEST_SUITE[0], "failed"), scored(TEST, TEST_SUITE[1], "failed")]


def locked(tmp_path):
    suite_sha = save_suite(tmp_path / "suite.json", TEST_SUITE)
    manifest = Manifest(episodes=(DEV, TEST), prompts={"p": "a" * 64}, thresholds={}, attack_suite_sha256=suite_sha)
    path = tmp_path / "manifest.json"
    return path, lock(manifest, path)


def seal(tmp_path, session, run, **override):
    kwargs = dict(dev_before=[DEV_FAIL], dev_after=[scored(DEV, DEV_FAIL.score.record.script, "avoided")],
                  holdout_before=HOLDOUT_BEFORE, holdout_after=HOLDOUT_AFTER)
    return session.seal(run, tmp_path / "suite.json", **(kwargs | override))


def sealed_trial(tmp_path, session):
    return seal(tmp_path, session, session.open_holdout(*locked(tmp_path)))


def test_trial_sealed_after_holdout(tmp_path):
    path, pinned = locked(tmp_path)
    session = session_for(path)
    propose(session, FakeGateway(change("brake_lead_s", 2.0)))
    outside = FinalRun(path, pinned)
    with pytest.raises(HoldoutError, match="no tuning"):  # any FinalRun on the manifest stops tuning
        propose(session, FakeGateway(change("brake_lead_s", 2.5)))
    with pytest.raises(HoldoutError, match="open_holdout"):  # only the session's own run can seal
        seal(tmp_path, session, outside)
    trial = seal(tmp_path, session, session.open_holdout(path, pinned))
    assert trial.diff == {"brake_lead_s": (1.0, 2.0)}
    assert trial.manifest_full_sha256 == session.run.full_sha256
    with pytest.raises(HoldoutError, match="already sealed"):
        seal(tmp_path, session, session.run)
    ledger = ResultsLedger(tmp_path / "repairs.jsonl", RepairTrial)
    ledger.append(trial)
    ledger.append(trial)
    assert len(ledger.read()) == 2 and ledger.read()[0].proposals[0].request_id == "req-1"


def test_regressions_and_unsuccessful_fixes_reported(tmp_path):
    session = session_for(tmp_path / "manifest.json")
    propose(session, FakeGateway(change("brake_lead_s", 2.0)))
    trial = sealed_trial(tmp_path, session)
    assert trial.dev_delta[PED.value] == 1.0 and trial.regressions["dev"] == []
    assert trial.holdout_delta[TRUCK.value] == -1.0 and trial.regressions["holdout"] == [TRUCK.value, "overall"]
    assert trial.unsuccessful_fixes == [PED.value]  # fixed on dev, still failing on held-out cameras


def test_open_holdout_blocks_tuning(tmp_path):
    path, pinned = locked(tmp_path)
    session = session_for(path)
    session.open_holdout(path, pinned)
    with pytest.raises(HoldoutError, match="no tuning"):
        propose(session, FakeGateway(change("brake_lead_s", 2.0)))


def test_seal_rejects_cherry_picked_holdout_and_test_dev_scores(tmp_path):
    session = session_for(tmp_path / "manifest.json")
    run = session.open_holdout(*locked(tmp_path))
    with pytest.raises(ValueError, match="same"):  # dropped the slot that regressed
        seal(tmp_path, session, run, holdout_after=HOLDOUT_AFTER[1:])
    with pytest.raises(ValueError, match="same"):  # same count, different slots
        seal(tmp_path, session, run, holdout_after=[HOLDOUT_AFTER[1], HOLDOUT_AFTER[1]])
    with pytest.raises(ValueError, match="dev-split"):
        seal(tmp_path, session, run, dev_after=[scored(TEST, DEV_FAIL.score.record.script, "avoided")])
    assert not session.sealed


def test_dev_before_after_must_be_paired(tmp_path):
    session = session_for(tmp_path / "manifest.json")
    propose(session, FakeGateway(change("brake_lead_s", 2.0)))
    run = session.open_holdout(*locked(tmp_path))
    with pytest.raises(ValueError, match="dev before/after"):  # an after-set with the failure dropped
        seal(tmp_path, session, run, dev_after=[])


def test_seal_pairs_replicates_across_distinct_runtime_sessions(tmp_path):
    session = session_for(tmp_path / "manifest.json")
    run = session.open_holdout(*locked(tmp_path))

    def rows(episode, scripts, variant):
        return [scored(episode, script, "failed" if variant == "before" else "avoided",
                       comparison_id=replicate, session_id=f"{variant}-{episode.episode_id}-{replicate}")
                for replicate in ("replicate-1", "replicate-2") for script in scripts]

    dev_before, dev_after = (rows(DEV, [DEV_FAIL.score.record.script], variant) for variant in ("before", "after"))
    holdout_before, holdout_after = (rows(TEST, TEST_SUITE, variant) for variant in ("before", "after"))
    trial = seal(tmp_path, session, run, dev_before=dev_before, dev_after=list(reversed(dev_after)),
                 holdout_before=holdout_before, holdout_after=list(reversed(holdout_after)))
    assert trial.dev_before["overall"]["n"] == trial.dev_after["overall"]["n"] == 2
    assert trial.holdout_before["overall"]["n"] == trial.holdout_after["overall"]["n"] == 4
    assert trial.holdout_delta["overall"] == 1
    assert {i.score.record.session_id for i in holdout_before}.isdisjoint(
        i.score.record.session_id for i in holdout_after)


def test_seal_rejects_replaced_and_duplicated_replicates(tmp_path):
    session = session_for(tmp_path / "manifest.json")
    run = session.open_holdout(*locked(tmp_path))
    replaced = [i.model_copy(update={"comparison_id": "different-replicate"}) for i in HOLDOUT_AFTER]
    with pytest.raises(ValueError, match="same.*comparison"):
        seal(tmp_path, session, run, holdout_after=replaced)
    with pytest.raises(ValueError, match="unique"):
        seal(tmp_path, session, run, holdout_before=HOLDOUT_BEFORE * 2, holdout_after=HOLDOUT_AFTER * 2)
    assert not session.sealed


def test_comparison_id_is_explicit_not_inferred_from_runtime_session():
    data = DEV_FAIL.model_dump()
    del data["comparison_id"]
    with pytest.raises(ValueError, match="comparison_id"):
        ScoredEpisode.model_validate(data)


class GatedGateway(FakeGateway):
    """Holds every call until `release` is set, so proposals overlap."""

    def __init__(self, *outputs):
        super().__init__(*outputs)
        self.release = asyncio.Event()

    async def generate(self, *args, **kwargs):
        await self.release.wait()
        return await super().generate(*args, **kwargs)


def test_concurrent_proposals_respect_cap():
    session, gateway = session_for(), GatedGateway(*[change("brake_lead_s", 2.0)] * 3)

    async def run():
        calls = [asyncio.create_task(session.propose(gateway, CONTEXT)) for _ in range(3)]
        await asyncio.sleep(0)
        gateway.release.set()
        return await asyncio.gather(*calls, return_exceptions=True)

    results = asyncio.run(run())
    assert sum(isinstance(r, RuntimeError) for r in results) == 1
    assert sorted(p.round for p in session.proposals) == [1, 2]


def test_holdout_opened_mid_proposal_rejects_it(tmp_path):
    path, pinned = locked(tmp_path)
    session, gateway = session_for(path), GatedGateway(change("brake_lead_s", 2.0))

    async def run():
        call = asyncio.create_task(session.propose(gateway, CONTEXT))
        await asyncio.sleep(0)
        session.open_holdout(path, pinned)
        gateway.release.set()
        return await call

    record = asyncio.run(run())
    assert not record.accepted and "holdout" in record.reason
    assert session.values == VALUES and session.proposals == [record]
