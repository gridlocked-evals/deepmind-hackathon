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


def scored(episode, script, outcome, actions=()):
    record = AttackRecord(script=script, session_id=episode.episode_id, send_frame=script.trigger_frame,
                          source="adaptive" if episode.split == "dev" else "frozen")
    score = AttackScore(record=record, outcome=outcome, first_visible_frame=60, contact_frame=80 if outcome == "failed" else None,
                        lead_frames=20, required_lead_frames=36, sample_every=6, window_end=300)
    return ScoredEpisode(episode=episode, score=score, defender_actions=actions)


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


def propose(session, gateway):
    return asyncio.run(session.propose(gateway, CONTEXT))


def test_out_of_surface_and_out_of_bounds_edits_rejected():
    for field, value in [("contact_threshold", .9), ("brake_lead_s", 9.0), ("brake_lead_s", "2"), ("mode", "panic"),
                         ("hazard_note", "x" * 41)]:
        session = RepairSession(SURFACE, VALUES, [DEV_FAIL], TEST_SUITE)
        record = propose(session, FakeGateway(change(field, value)))
        assert not record.accepted and record.request_id == "req-1" and session.values == VALUES
    session = RepairSession(SURFACE, VALUES, [DEV_FAIL], TEST_SUITE)
    assert propose(session, FakeGateway(change("brake_lead_s", 2.0))).accepted and session.values["brake_lead_s"] == 2.0


def test_packet_has_no_adversary_or_test_data():
    packet = build_packet([DEV_FAIL], TEST_SUITE)
    assert packet[0]["hazard"] == PED.value and packet[0]["defender_actions"] == ["hold_forward", "release_keys"]
    gateway = FakeGateway(change("mode", "alert"))
    propose(RepairSession(SURFACE, VALUES, [DEV_FAIL], TEST_SUITE), gateway)
    for text in (json.dumps(packet), gateway.inputs[0]):
        for secret in (DEV_TEXT, TEST_TEXT, "steering_text", "attack_type", "send_frame", "trigger_frame"):
            assert secret not in text
    leaky = DEV_FAIL.model_copy(update={"defender_actions": ("heard: " + DEV_TEXT,)})
    with pytest.raises(ValueError, match="steering text"):
        build_packet([leaky], TEST_SUITE)


def test_test_split_source_raises():
    with pytest.raises(HoldoutError):
        build_packet([scored(ep("t1", "test", "cam9"), TEST_SUITE[0], "failed")], TEST_SUITE)
    with pytest.raises(HoldoutError):  # a frozen-suite script replayed on a dev camera is still test data
        build_packet([scored(DEV, TEST_SUITE[0], "failed")], TEST_SUITE)


def test_attempt_cap_and_every_proposal_recorded():
    session = RepairSession(SURFACE, VALUES, [DEV_FAIL], TEST_SUITE)
    propose(session, FakeGateway(change("physics_gravity", 1.0)))
    propose(session, FakeGateway(change("brake_lead_s", 2.0)))
    with pytest.raises(RuntimeError, match="cap"):
        propose(session, FakeGateway(change("brake_lead_s", 2.5)))
    assert [(p.round, p.accepted) for p in session.proposals] == [(1, False), (2, True)]


def final_run(tmp_path):
    suite_sha = save_suite(tmp_path / "suite.json", TEST_SUITE)
    manifest = Manifest(episodes=(DEV, ep("b1", "test", "cam9")), prompts={"p": "a" * 64}, thresholds={},
                        attack_suite_sha256=suite_sha)
    return FinalRun(tmp_path / "manifest.json", lock(manifest, tmp_path / "manifest.json"))


def sealed_trial(tmp_path, session):
    test = ep("b1", "test", "cam9")
    return session.seal(final_run(tmp_path), tmp_path / "suite.json",
                        dev_before=[DEV_FAIL.score], dev_after=[scored(DEV, DEV_FAIL.score.record.script, "avoided").score],
                        holdout_before=[scored(test, TEST_SUITE[0], "avoided"), scored(test, TEST_SUITE[1], "failed")],
                        holdout_after=[scored(test, TEST_SUITE[0], "failed"), scored(test, TEST_SUITE[1], "failed")])


def test_trial_sealed_after_holdout(tmp_path):
    session = RepairSession(SURFACE, VALUES, [DEV_FAIL], TEST_SUITE)
    propose(session, FakeGateway(change("brake_lead_s", 2.0)))
    trial = sealed_trial(tmp_path, session)
    assert trial.diff == {"brake_lead_s": (1.0, 2.0)}
    with pytest.raises(HoldoutError, match="sealed"):
        propose(session, FakeGateway(change("brake_lead_s", 2.5)))
    with pytest.raises(HoldoutError):
        session.seal(None, tmp_path / "suite.json", dev_before=[], dev_after=[], holdout_before=[], holdout_after=[])
    ledger = ResultsLedger(tmp_path / "repairs.jsonl", RepairTrial)
    ledger.append(trial)
    ledger.append(trial)
    assert len(ledger.read()) == 2 and ledger.read()[0].proposals[0].request_id == "req-1"


def test_regressions_and_unsuccessful_fixes_reported(tmp_path):
    session = RepairSession(SURFACE, VALUES, [DEV_FAIL], TEST_SUITE)
    propose(session, FakeGateway(change("brake_lead_s", 2.0)))
    trial = sealed_trial(tmp_path, session)
    assert trial.dev_delta[PED.value] == 1.0 and trial.regressions["dev"] == []
    assert trial.holdout_delta[TRUCK.value] == -1.0 and trial.regressions["holdout"] == [TRUCK.value, "overall"]
    assert trial.unsuccessful_fixes == [PED.value]  # fixed on dev, still failing on held-out cameras
