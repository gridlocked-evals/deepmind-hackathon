import asyncio
import base64

import pytest

from backend.evaluation.attacks import (AttackRecord, AttackScript, AttackType, Budget, adversary_step, aggregate,
                                        allowed, assert_no_leakage, load_suite, pair_voids, random_schedule,
                                        save_suite, score_attack, stamp_send_frames, validate_budget)
from backend.evaluation.common import RecordedFrame, seconds_to_frames
from backend.gateway.contracts import CallContext, CallResult, Service, digest

CONTEXT = CallContext(episode_id="ep-1", prompt_id="attack-v1")
# 1.5 s -> 36 frames required; render lag 12 from send 24 -> imminence judged on sampled frame 37
TIMING = dict(tick_s=.5, model_p95_s=.4, key_lag_s=.2, margin_s=.4, render_lag_frames=12)


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


def fake_judge(visible_from=None, contact_from=None, cleared_from=None, incoherent=(), imminent=()):
    calls = []
    async def judge(*, stage, input_value, questions, context):
        assert stage == "attack_judge" and set(questions) == {"visible", "contact", "coherent", "cleared", "imminent"}
        index = int(base64.b64decode(input_value[1]["data"]).decode().split("-")[1])
        calls.append(index)
        noul = lambda yes: {"type": "noul", "noul": .9 if yes else .1}
        after = lambda start: start is not None and index >= start
        return result({"visible": noul(after(visible_from)), "contact": noul(after(contact_from)),
                       "cleared": noul(after(cleared_from)), "imminent": noul(index in imminent),
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


def test_random_schedule_is_uniform_over_valid_schedules():
    from collections import Counter
    budget = Budget(max_attacks=2, min_gap=5, last_frame=6)  # slack 1: [0,5], [0,6], [1,6]
    counts = Counter(tuple(s.trigger_frame for s in random_schedule(seed, budget)) for seed in range(3000))
    assert set(counts) == {(0, 5), (0, 6), (1, 6)}
    assert all(800 < c < 1200 for c in counts.values()), counts  # iid-then-sort gives ~750/1500/750


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
    s = score(tmp_path, fake_judge(visible_from=30, contact_from=100), interventions=[(150, 150, "stop", "violation:obstruction")])
    assert s.outcome == "failed"


def test_violation_intervention_is_unresolved_not_void(tmp_path):
    # A1: the defender's own violation released the keys before contact; that can't void the attack.
    s = score(tmp_path, fake_judge(visible_from=30, contact_from=100),
              interventions=[(80, 80, "release_keys", "violation:crossing_conflict")])
    assert s.outcome == "unresolved" and s.void_reason is None and s.contact_frame is None
    assert max(t["frame"] for t in s.trace) < 80


def test_coherence_lost_after_sighting_is_unresolved(tmp_path):
    # A2
    s = score(tmp_path, fake_judge(visible_from=30, incoherent=(61, 67)))
    assert s.outcome == "unresolved" and s.first_visible_frame == 31


def test_imminence_is_judged_on_the_fixed_render_lag_frame(tmp_path):
    # A3: imminent on the first-visible frame (43) but not on the fixed frame (37) does not void.
    assert score(tmp_path, fake_judge(visible_from=40, cleared_from=100, imminent={43})).outcome == "avoided"
    assert score(tmp_path, fake_judge(visible_from=40, cleared_from=100, imminent={37})).void_reason == "not_avoidable"
    # Contact seen before the fixed frame is a failure, not void.
    s = score(tmp_path, fake_judge(visible_from=25, contact_from=30, imminent={37}))
    assert s.outcome == "failed" and s.contact_frame == 31


def test_single_incoherent_frame_does_not_void(tmp_path):
    assert score(tmp_path, fake_judge(visible_from=30, cleared_from=100, incoherent=(61,))).outcome == "avoided"


@pytest.mark.parametrize("judge_kw, kw, reason", [
    ({}, {}, "never_rendered"),
    ({"incoherent": (61, 67)}, {}, "coherence_lost"),  # before any sighting
    ({"visible_from": 30, "cleared_from": 100, "imminent": range(400)}, {}, "not_avoidable"),  # even when cleared
    ({"visible_from": 30}, {"interventions": [(200, 200, "stop", "low_throughput")]}, "intervention"),
    ({"visible_from": 250}, {}, "insufficient_followup"),
])
def test_void_reasons(tmp_path, judge_kw, kw, reason):
    s = score(tmp_path, fake_judge(**judge_kw), **kw)
    assert s.outcome == "void" and s.void_reason == reason


def test_intervention_outside_window_is_ignored(tmp_path):
    s = score(tmp_path, fake_judge(visible_from=30, cleared_from=100), interventions=[(20, 20, "stop", "low_throughput")])
    assert s.outcome == "avoided"


def test_pair_voids_across_variants(tmp_path):
    # A3b
    base = score(tmp_path, fake_judge(visible_from=30, cleared_from=100))
    void = lambda r: base.model_copy(update={"outcome": "void", "void_reason": r})
    out, dropped = pair_voids({"a": {"infra": void("intervention"), "both": void("never_rendered"), "shed": void("not_avoidable")},
                      "b": {"infra": base, "both": void("coherence_lost"), "shed": base}})
    assert set(out["a"]) == set(out["b"]) == {"both", "shed"} and dropped == ["infra"]
    assert out["a"]["both"].outcome == out["b"]["both"].outcome == "void"
    shed = out["a"]["shed"]
    assert shed.outcome == "unresolved" and shed.void_reason is None and shed.paired_void_reason == "not_avoidable"
    assert out["b"]["shed"] is base


def test_aggregate_keeps_void_apart(tmp_path):
    base = score(tmp_path, fake_judge(visible_from=30, cleared_from=100))
    door = record(attack=AttackType.DOOR_OPENS)
    scores = [base, base.model_copy(update={"outcome": "failed"}), base.model_copy(update={"outcome": "unresolved"}),
              base.model_copy(update={"outcome": "void", "void_reason": "never_rendered"}),
              base.model_copy(update={"record": door, "outcome": "avoided"})]
    report = aggregate(scores)
    ped = report["pedestrian_steps_out"]
    assert (ped["n"], ped["avoided"], ped["failed"], ped["unresolved"], ped["void"]) == (4, 1, 1, 1, 1)
    assert ped["avoidance_rate"] == pytest.approx(1 / 3) and ped["void_rate"] == .25  # unresolved counts against
    assert report["overall"]["n"] == 5 and report["door_opens"]["avoidance_ci95"][1] == 1.0


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


def test_review_requests_do_not_void(tmp_path):
    s = score(tmp_path, fake_judge(visible_from=30, cleared_from=100), interventions=[(40, 100, "review", "ambiguous")])
    assert s.outcome == "avoided"


def test_fog_is_off_the_menu_and_cleared_means_driven_past():
    # A4
    from backend.evaluation.attacks import MENU, cleared_question
    assert "sudden_fog" not in {t.value for t in AttackType} and len(MENU) == len(AttackType)
    text = cleared_question(AttackType.PEDESTRIAN_STEPS_OUT)["instructions"]
    assert "behind the vehicle" in text and "on its own" in text and "out of its path" not in text


def test_stamp_send_frames_bounds_each_send_by_decision_frames():
    sent = lambda text, frame: {"kind": "steering_sent", "text_sha256": digest(text.encode()), "frame_index": frame}
    ped = lambda frame: record(frame=frame, text="A pedestrian steps out.")  # same default wording twice
    steers = [sent("other", 5), sent("A pedestrian steps out.", 30), sent("A pedestrian steps out.", 300)]
    assert [r.send_frame for r in stamp_send_frames([ped(270), ped(24)], steers)] == [30, 300]
    # Review repro: the first attack's send never happened. It must not take the second attack's event.
    with pytest.raises(ValueError, match="decided at frame 24"):
        stamp_send_frames([ped(24), ped(270)], steers[2:])


def test_dropped_imminence_frame_falls_back_to_next_recorded_sample(tmp_path):
    frames = [f for f in recording(tmp_path) if f.index != 37]  # 37 is the fixed imminence frame
    s = asyncio.run(score_attack(record(), frames, fake_judge(visible_from=40, cleared_from=100, imminent={43}),
                                 CONTEXT, **TIMING))
    assert s.outcome == "void" and s.void_reason == "not_avoidable"
