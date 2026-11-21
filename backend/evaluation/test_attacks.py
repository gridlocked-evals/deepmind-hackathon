import asyncio
import base64

import pytest

from backend.evaluation.attacks import (AttackRecord, AttackScript, AttackType, Budget, adversary_step, aggregate,
                                        allowed, assert_no_leakage, load_suite, random_schedule, save_suite,
                                        score_attack, validate_budget)
from backend.evaluation.common import RecordedFrame, seconds_to_frames
from backend.gateway.contracts import CallContext, CallResult, Service

CONTEXT = CallContext(episode_id="ep-1", prompt_id="attack-v1")
TIMING = dict(tick_s=.5, model_p95_s=.4, key_lag_s=.2, margin_s=.4)  # 1.5 s -> 36 frames required


def result(output, n=0):
    return CallResult(f"req-{n}", None, "m", "h", "h", output, None, "reservation_retained")


def record(frame=24, attack=AttackType.PEDESTRIAN_STEPS_OUT, text="A pedestrian steps out."):
    return AttackRecord(script=AttackScript(attack_type=attack, steering_text=text, trigger_frame=frame),
                        session_id="s1", send_frame=frame, source="frozen")


def recording(tmp_path, n=400):
    frames = []
    for i in range(n):
        path = tmp_path / f"{i}.jpg"
        path.write_bytes(f"frame-{i}".encode())
        frames.append(RecordedFrame("s1", i, "x", str(path)))
    return frames


def fake_judge(visible_from=None, contact_from=None, cleared_from=None, incoherent=(), imminent=False):
    calls = []
    async def judge(*, stage, input_value, questions, context):
        assert stage == "attack_judge" and set(questions) == {"visible", "contact", "coherent", "cleared", "imminent"}
        index = int(base64.b64decode(input_value[1]["data"]).decode().split("-")[1])
        calls.append(index)
        noul = lambda yes: {"type": "noul", "noul": .9 if yes else .1}
        after = lambda start: start is not None and index >= start
        return result({"visible": noul(after(visible_from)), "contact": noul(after(contact_from)),
                       "cleared": noul(after(cleared_from)), "imminent": noul(imminent),
                       "coherent": noul(index not in incoherent)}, index)
    judge.calls = calls
    return judge


def score(tmp_path, judge, **kw):
    return asyncio.run(score_attack(record(), recording(tmp_path), judge, CONTEXT, **TIMING, **kw))


def test_budget_rules():
    gap, end = seconds_to_frames(10), seconds_to_frames(90)
    validate_budget([0, gap, 2 * gap, end])
    for bad in ([0, gap - 1], [end + 1], [0, gap, 2 * gap, 3 * gap, 4 * gap]):
        with pytest.raises(ValueError):
            validate_budget(bad)
    assert not allowed([100], 100 + gap - 1) and allowed([100], 100 + gap)


def test_random_schedule_is_seeded_and_within_budget():
    a, b = random_schedule(7), random_schedule(7)
    assert a == b and len(a) == 4 and a != random_schedule(8)
    validate_budget([s.trigger_frame for s in a])


def test_suite_roundtrip_and_tamper(tmp_path):
    path = tmp_path / "suite.json"
    sha = save_suite(path, random_schedule(1))
    assert load_suite(path, sha) == random_schedule(1)
    path.write_text(path.read_text().replace("trigger_frame\": ", "trigger_frame\": 1"))
    with pytest.raises(ValueError):
        load_suite(path)


class FakeGateway:
    def __init__(self, output):
        self.output, self.calls = output, []
    async def generate(self, service, *, stage, input_value, context, response_model=None):
        self.calls.append((service, stage))
        return result(self.output)


def test_adversary_step_attacks_waits_and_enforces_budget():
    image = {"type": "image", "mime_type": "image/jpeg", "data": "eA=="}
    step = lambda gw, frame, log=[]: asyncio.run(adversary_step(gw, frames=[image], frame_index=frame,
        defender_actions=["W"], log=log, session_id="s1", context=CONTEXT))
    gw = FakeGateway({"action": "door_opens"})
    attack = step(gw, 50)
    assert gw.calls == [(Service.CARBON, "adversary")]
    assert attack.send_frame == 50 and attack.source == "adaptive" and attack.script.attack_type == AttackType.DOOR_OPENS
    assert step(FakeGateway({"action": "wait"}), 50) is None
    # Too soon after the last attack: no model call at all.
    gw = FakeGateway({"action": "door_opens"})
    assert step(gw, 60, [attack]) is None and gw.calls == []


def test_avoided_needs_a_positive_clear(tmp_path):
    judge = fake_judge(visible_from=40, cleared_from=100)
    s = score(tmp_path, judge)
    assert s.outcome == "avoided" and s.first_visible_frame == 43 and s.cleared_frame == 103 and s.sample_every == 6
    assert judge.calls[:3] == [25, 31, 37] and s.window_end == 24 + 240 and s.lead_frames == 103 - 43
    assert s.required_lead_frames == 36 and len(s.trace) == len(judge.calls)


def test_stalling_defender_is_unresolved_not_avoided(tmp_path):
    s = score(tmp_path, fake_judge(visible_from=40))
    assert s.outcome == "unresolved" and s.void_reason is None


@pytest.mark.parametrize("judge_kw, contact", [
    ({"visible_from": 30, "contact_from": 100}, 103),
    ({"visible_from": 30, "contact_from": 40}, 43),  # a fast crash still counts
    ({"contact_from": 100}, 103),  # contact without a detected sighting still counts
    ({"visible_from": 30, "contact_from": 100, "incoherent": (115, 121)}, 103),  # crash before coherence loss
])
def test_failures_are_kept(tmp_path, judge_kw, contact):
    s = score(tmp_path, fake_judge(**judge_kw))
    assert s.outcome == "failed" and s.contact_frame == contact


def test_contact_before_intervention_is_kept(tmp_path):
    s = score(tmp_path, fake_judge(visible_from=30, contact_from=100), interventions=[(150, 170, "stop")])
    assert s.outcome == "failed"


def test_single_incoherent_frame_does_not_void(tmp_path):
    assert score(tmp_path, fake_judge(visible_from=30, cleared_from=100, incoherent=(61,))).outcome == "avoided"


@pytest.mark.parametrize("judge_kw, kw, reason", [
    ({}, {}, "never_rendered"),
    ({"visible_from": 30, "incoherent": (61, 67)}, {}, "coherence_lost"),
    ({"visible_from": 30, "cleared_from": 100, "imminent": True}, {}, "not_avoidable"),  # even when cleared
    ({"visible_from": 30}, {"interventions": [(200, 220, "stop")]}, "intervention"),
    ({"visible_from": 250}, {}, "insufficient_followup"),
])
def test_void_reasons(tmp_path, judge_kw, kw, reason):
    s = score(tmp_path, fake_judge(**judge_kw), **kw)
    assert s.outcome == "void" and s.void_reason == reason


def test_intervention_outside_window_is_ignored(tmp_path):
    s = score(tmp_path, fake_judge(visible_from=30, cleared_from=100), interventions=[(0, 20, "stop")])
    assert s.outcome == "avoided"


def test_aggregate_keeps_void_apart(tmp_path):
    base = score(tmp_path, fake_judge(visible_from=30, cleared_from=100))
    fog = record(attack=AttackType.SUDDEN_FOG)
    scores = [base, base.model_copy(update={"outcome": "failed"}), base.model_copy(update={"outcome": "unresolved"}),
              base.model_copy(update={"outcome": "void", "void_reason": "never_rendered"}),
              base.model_copy(update={"record": fog, "outcome": "avoided"})]
    report = aggregate(scores)
    ped = report["pedestrian_steps_out"]
    assert (ped["n"], ped["avoided"], ped["failed"], ped["unresolved"], ped["void"]) == (4, 1, 1, 1, 1)
    assert ped["avoidance_rate"] == pytest.approx(1 / 3) and ped["void_rate"] == .25  # unresolved counts against
    assert report["overall"]["n"] == 5 and report["sudden_fog"]["avoidance_ci95"][1] == 1.0


def test_leakage_check():
    records = [record(text="A pedestrian suddenly steps off the curb")]
    assert_no_leakage({"frame_index": 3, "keys": ["W"], "image": "..."}, records)
    for bad in ({"obs": {"steering_text": "x"}}, {"note": "a PEDESTRIAN suddenly steps off the curb now"},
                '{"history": [{"attack_type": "door_opens"}]}'):
        with pytest.raises(ValueError):
            assert_no_leakage(bad, records)


def test_adversary_stamps_send_frame_after_carbon_answers():
    image = {"type": "image", "mime_type": "image/jpeg", "data": "eA=="}
    run = lambda now, log=[]: asyncio.run(adversary_step(FakeGateway({"action": "door_opens"}), frames=[image],
        frame_index=50, defender_actions=[], log=log, session_id="s1", context=CONTEXT, current_frame=lambda: now))
    assert run(110).send_frame == 110 and run(110).script.trigger_frame == 110
    # The decision started in time, but Carbon answered after the 90 s limit.
    assert run(seconds_to_frames(90) + 1) is None
