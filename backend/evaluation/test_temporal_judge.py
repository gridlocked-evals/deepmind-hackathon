import asyncio
from types import SimpleNamespace

from backend.evaluation.common import RecordedFrame
from backend.evaluation.temporal_judge import (CLEAR, OBSERVED, UNKNOWN, Params, TemporalJudge, Window,
                                               classify)
from backend.gateway.contracts import CallContext, ErrorCode, GatewayError

CONTEXT = CallContext(episode_id="ep-1", prompt_id="temporal-v1")
IMAGE = {"type": "image", "mime_type": "image/jpeg", "data": "AA=="}


def noul(p, c=.9):
    return {"type": "noul", "noul": p, "confidence": c}


def answers(bus=.1, obstruction=.1, crossing=.1, ambiguous=.1):
    return {"bus_lane_entry": noul(bus), "obstruction": noul(obstruction),
            "crossing_conflict": noul(crossing), "ambiguous": noul(ambiguous)}


class FakeJudge:
    """reply(n) -> answers dict or exception for the n-th call; gate holds calls open."""

    def __init__(self, reply=lambda n: answers(), gate: asyncio.Event | None = None):
        self.reply, self.gate, self.calls = reply, gate, []

    async def __call__(self, *, stage, input_value, questions, context):
        self.calls.append({"stage": stage, "input": input_value, "questions": questions})
        n = len(self.calls) - 1
        if self.gate:
            await self.gate.wait()
        out = self.reply(n)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(output=out)


class FakeController:
    def __init__(self):
        self.log = []

    def release_keys(self):
        self.log.append(("release_keys",))

    def stop(self, reason):
        self.log.append(("stop", reason))

    def request_review(self, event):
        self.log.append(("review", event["start_frame"], event["end_frame"]))


def make(judge, **params):
    controller = FakeController()
    return TemporalJudge(judge, controller, CONTEXT, Params(**params), image=lambda f: IMAGE), controller


async def feed(tj, start, end):
    for i in range(start, end + 1):
        tj.on_frame(RecordedFrame("s", i, "h", f"/f/{i}.jpg"))
        await asyncio.sleep(0)


def test_cadence_windows_and_input():
    async def run():
        judge = FakeJudge()
        tj, _ = make(judge)
        await feed(tj, 0, 59 + 3 * 24)
        await tj.drain()
        assert [(w.start, w.end) for w in tj.windows] == [(0, 59), (24, 83), (48, 107), (72, 131)]
        assert tj.windows[0].frames == [0, 20, 39, 59]
        call = judge.calls[0]
        assert call["stage"] == "temporal_gate" and set(call["questions"]) == {
            "bus_lane_entry", "obstruction", "crossing_conflict", "ambiguous"}
        assert [p for p in call["input"] if p["type"] == "image"] == [IMAGE] * 4
        assert all(w.outcomes == dict.fromkeys(w.outcomes, CLEAR) for w in tj.windows)
    asyncio.run(run())


def test_classify_typed_outcomes():
    p = Params()
    assert classify(answers(bus=.9, obstruction=.5), p) == {
        "bus_lane_entry": OBSERVED, "obstruction": UNKNOWN, "crossing_conflict": CLEAR}
    assert set(classify(answers(bus=.9, ambiguous=.8), p).values()) == {UNKNOWN}
    low_conf = answers(bus=.9) | {"crossing_conflict": noul(.05, c=.2)}
    assert classify(low_conf, p)["crossing_conflict"] == UNKNOWN
    missing = answers()
    del missing["obstruction"]
    assert classify(missing, p)["obstruction"] == UNKNOWN
    assert set(classify(None, p).values()) == {UNKNOWN}


def test_failed_check_is_insufficient_and_reduces_cadence():
    async def run():
        tj, controller = make(FakeJudge(lambda n: GatewayError(ErrorCode.TIMEOUT)))
        await feed(tj, 0, 59)
        await tj.drain()
        w = tj.windows[0]
        assert w.error == ErrorCode.TIMEOUT and set(w.outcomes.values()) == {UNKNOWN}
        assert controller.log == [] and tj.stride == 48
        assert tj.coverage()["failed"] == 1 and tj.coverage()["judged"] == 0
    asyncio.run(run())


def test_low_throughput_stops_session():
    async def run():
        tj, controller = make(FakeJudge(lambda n: RuntimeError("down")))
        await feed(tj, 0, 600)
        await tj.drain()
        assert controller.log == [("stop", "low_throughput")]
        assert tj.stopped == "low_throughput"
        n = len(tj.windows)
        await feed(tj, 601, 900)
        assert len(tj.windows) == n  # nothing scheduled after stop
        assert tj.interventions[-1][2] == "stop"
    asyncio.run(run())


def test_in_flight_bound_skips_and_counts():
    async def run():
        gate = asyncio.Event()
        judge = FakeJudge(gate=gate)
        tj, _ = make(judge, max_in_flight=1, max_stride=10_000, stale=10_000)
        await feed(tj, 0, 59 + 24 * 2)  # due at 59, 83, then 83+48=131 after one skip
        assert len(judge.calls) == 1 and tj.skipped == [(24, 83)]
        gate.set()
        await tj.drain()
        cov = tj.coverage()
        assert cov["skipped"] == 1 and cov["due"] == 2 and cov["judged"] == 1
    asyncio.run(run())


def test_stale_result_is_logged_but_cannot_act():
    async def run():
        gate = asyncio.Event()
        tj, controller = make(FakeJudge(lambda n: answers(bus=.95), gate=gate), max_stride=10_000)
        await feed(tj, 0, 59)
        await feed(tj, 60, 59 + 49)  # newest is now 49 frames past the window end (> stale=48)
        gate.set()
        await tj.drain()
        assert tj.windows[0].stale and tj.windows[0].outcomes["bus_lane_entry"] == OBSERVED
        assert controller.log == [] and tj.interventions == []
        assert tj.coverage()["stale"] == 1
    asyncio.run(run())


def test_actions_release_stop_and_review():
    async def run():
        tj, controller = make(FakeJudge(lambda n: answers(bus=.9)), stop_after=3)
        await feed(tj, 0, 59 + 24 * 2)
        await tj.drain()
        assert controller.log == [("release_keys",)] * 3 + [("stop", "repeated_violation:bus_lane_entry")]
        assert tj.interventions == [(0, 59, "release_keys"), (24, 83, "release_keys"),
                                    (48, 107, "release_keys"), (48, 107, "stop")]

        tj, controller = make(FakeJudge(lambda n: answers(ambiguous=.9)))
        await feed(tj, 0, 59)
        await tj.drain()
        assert controller.log == [("review", 0, 59)] and tj.interventions == [(0, 59, "review")]
    asyncio.run(run())


def test_event_grouping_and_interval_bounds():
    tj, _ = make(FakeJudge())
    obs = {"bus_lane_entry": OBSERVED, "obstruction": CLEAR, "crossing_conflict": CLEAR}
    other = {"bus_lane_entry": CLEAR, "obstruction": OBSERVED, "crossing_conflict": CLEAR}
    tj.windows = [Window(24, 83, [], obs), Window(0, 59, [], obs),  # overlapping, out of order
                  Window(84, 143, [], obs),  # adjacent -> same event
                  Window(300, 359, [], obs),  # gap -> new event
                  Window(24, 83, [], other)]
    assert tj.events() == [
        {"type": "bus_lane_entry", "start_lo": 0, "start_hi": 59, "end_lo": 84, "end_hi": 143, "windows": 3},
        {"type": "bus_lane_entry", "start_lo": 300, "start_hi": 359, "end_lo": 300, "end_hi": 359, "windows": 1},
        {"type": "obstruction", "start_lo": 24, "start_hi": 83, "end_lo": 24, "end_hi": 83, "windows": 1}]


def test_coverage_ranges():
    async def run():
        fail_second = FakeJudge(lambda n: RuntimeError() if n == 1 else answers())
        tj, _ = make(fail_second, window=10, stride=10, max_stride=10_000, stale=10_000)
        await feed(tj, 0, 34)
        await tj.drain()
        assert [(w.start, w.end) for w in tj.windows] == [(0, 9), (10, 19)]  # failure halves cadence
        cov = tj.coverage()
        assert cov["covered"] == [(0, 9)] and cov["uncovered"] == [(10, 34)]
        assert (cov["due"], cov["judged"], cov["failed"], cov["skipped"]) == (2, 1, 1, 0)
    asyncio.run(run())
