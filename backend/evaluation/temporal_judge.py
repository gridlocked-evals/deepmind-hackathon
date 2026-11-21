"""Live temporal Decisions gate (#11).

Push frames with `on_frame`; about once per second a 2-3 s window of sampled
frames is judged via `judge(stage="temporal_gate")`. Accepted (fresh) results
drive an injected Controller. Sparse samples are never treated as proof of
safety: failures, ambiguity and skips are insufficient_evidence / uncovered.
All positions are frame indices (#9's clock).
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from backend.evaluation.common import GENIE_FPS, Judge, RecordedFrame, yes_probability
from backend.gateway.contracts import CallContext

VIOLATIONS = {
    "bus_lane_entry": "Across these frames, does the camera vehicle enter a marked bus lane?",
    "obstruction": "Across these frames, does the camera vehicle block or obstruct another road user, lane or crossing?",
    "crossing_conflict": "Across these frames, does the camera vehicle fail to yield to a pedestrian or vehicle at a crossing or intersection?",
}
AMBIGUITY = "ambiguous"
QUESTIONS = {**{k: {"type": "noul", "instructions": v} for k, v in VIOLATIONS.items()},
             AMBIGUITY: {"type": "noul", "instructions": "Is the view too occluded, blurry, dark or ambiguous to judge the camera vehicle's behaviour across these frames?"}}

OBSERVED, CLEAR, UNKNOWN = "observed_violation", "no_visible_violation", "insufficient_evidence"


class Controller(Protocol):
    def release_keys(self) -> None: ...
    def stop(self, reason: str) -> None: ...
    def request_review(self, event: dict) -> None: ...


@dataclass
class Params:
    """All durations in frames."""
    window: int = 2 * GENIE_FPS + GENIE_FPS // 2  # 2.5 s
    stride: int = GENIE_FPS  # ~1 Hz
    max_stride: int = 4 * GENIE_FPS  # cadence floor before stopping for low throughput
    samples: int = 4
    max_in_flight: int = 1
    stale: int = 2 * GENIE_FPS  # result older than this behind newest frame cannot act
    violation_p: float = 0.7
    clear_p: float = 0.3
    ambiguous_p: float = 0.5
    min_confidence: float = 0.5
    stop_after: int = 3  # windows in one violation event before stopping the session


@dataclass
class Window:
    start: int
    end: int
    frames: list[RecordedFrame]  # snapshotted: the rolling buffer may evict them before judging
    outcomes: dict[str, str] = field(default_factory=dict)
    answers: dict | None = None
    error: str | None = None
    stale: bool = False
    actionable: bool = False  # admitted to the controller when this answer arrived


def load_image(frame: RecordedFrame) -> dict:
    mime = "image/png" if frame.path.lower().endswith(".png") else "image/jpeg"
    return {"type": "image", "mime_type": mime, "data": base64.b64encode(Path(frame.path).read_bytes()).decode()}


def classify(answers: dict | None, p: Params) -> dict[str, str]:
    """Typed outcome per violation question; any missing/failed/ambiguous answer is UNKNOWN, never safe."""
    if not answers:
        return {k: UNKNOWN for k in VIOLATIONS}
    try:
        ambiguity_confidence = float(answers[AMBIGUITY].get("confidence", 0))
        if (not ambiguity_confidence >= p.min_confidence
                or yes_probability(answers[AMBIGUITY]) >= p.ambiguous_p):
            return {k: UNKNOWN for k in VIOLATIONS}
    except (KeyError, ValueError, TypeError):
        return {k: UNKNOWN for k in VIOLATIONS}
    out = {}
    for k in VIOLATIONS:
        try:
            prob, conf = yes_probability(answers[k]), float(answers[k].get("confidence", 0))
        except (KeyError, ValueError, TypeError):
            out[k] = UNKNOWN
            continue
        out[k] = (OBSERVED if prob >= p.violation_p and conf >= p.min_confidence else
                  CLEAR if prob <= p.clear_p and conf >= p.min_confidence else UNKNOWN)
    return out


def merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union of inclusive frame ranges, merging overlapping and adjacent ones."""
    out: list[list[int]] = []
    for s, e in sorted(ranges):
        if out and s <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [tuple(r) for r in out]


class TemporalJudge:
    def __init__(self, judge: Judge, controller: Controller, context: CallContext,
                 params: Params | None = None, image: Callable[[RecordedFrame], dict] = load_image):
        self.judge, self.controller, self.context = judge, controller, context
        self.p = params or Params()
        self.image = image
        self.stride = self.p.stride
        self.frames: dict[int, RecordedFrame] = {}
        self.newest = -1
        self.next_due = self.p.window - 1
        self.in_flight: set[asyncio.Task] = set()
        self.windows: list[Window] = []  # every due window, judged or not
        self.skipped: list[tuple[int, int]] = []
        self.interventions: list[tuple[int, int, str, str]] = []  # (start, end, action, reason) for #24's scorer
        self.stopped: str | None = None
        self.newest_answered = -1  # start of the newest window that got answers

    def on_frame(self, frame: RecordedFrame) -> None:
        self.newest = max(self.newest, frame.index)
        self.frames[frame.index] = frame
        for i in [i for i in self.frames if i <= self.newest - self.p.window]:
            del self.frames[i]
        if self.stopped or frame.index < self.next_due:
            return
        self.next_due = frame.index + self.stride
        start = frame.index - self.p.window + 1
        if sum(not t.done() for t in self.in_flight) >= self.p.max_in_flight:
            self.skipped.append((start, frame.index))
            self._miss()
            return
        n = self.p.samples
        picks = sorted({start + round(i * (self.p.window - 1) / (n - 1)) for i in range(n)})
        window = Window(start, frame.index, [self.frames[i] for i in picks if i in self.frames])
        self.windows.append(window)
        task = asyncio.get_running_loop().create_task(self._judge(window))
        self.in_flight.add(task)
        task.add_done_callback(self.in_flight.discard)

    async def drain(self) -> None:
        # done tasks may still sit in the set until their discard callback runs
        while pending := [t for t in self.in_flight if not t.done()]:
            await asyncio.gather(*pending)

    async def _judge(self, w: Window) -> None:
        parts = [{"type": "text", "text": f"{len(w.frames)} frames from a {GENIE_FPS} fps driving video, in order, "
                                          f"covering frames {w.start}-{w.end}."}]
        try:
            for f in w.frames:
                parts += [{"type": "text", "text": f"Frame {f.index}:"}, self.image(f)]
            result = await self.judge(stage="temporal_gate", input_value=parts, questions=QUESTIONS, context=self.context)
            w.answers = result.output
        except Exception as e:  # GatewayError, timeout, cancellation of the call, bad frame
            w.error = getattr(e, "code", None) or type(e).__name__
        w.outcomes = classify(w.answers, self.p)
        w.stale = self.newest - w.end > self.p.stale
        if w.error or w.stale:
            self._miss()
        else:
            self.stride = max(self.p.stride, self.stride // 2)
        superseded = w.start < self.newest_answered  # possible when max_in_flight > 1
        if w.answers:
            self.newest_answered = max(self.newest_answered, w.start)
        if w.stale or self.stopped or superseded:
            return  # logged in self.windows, but cannot act
        w.actionable = True
        self._act(w)

    def _miss(self) -> None:
        """Low throughput: halve cadence, then stop once at the floor."""
        if self.stride >= self.p.max_stride:
            self._stop("low_throughput", "low_throughput")
        else:
            self.stride = min(self.p.max_stride, self.stride * 2)
            self.next_due = max(self.next_due, self.newest + self.stride)

    def _stop(self, reason: str, logged: str) -> None:
        if not self.stopped:
            self.stopped = reason
            self.interventions.append((self.newest, self.newest, "stop", logged))
            self.controller.stop(reason)

    def _act(self, w: Window) -> None:
        hits = [k for k, v in w.outcomes.items() if v == OBSERVED]
        if hits:
            # Logged at the frame the keys were actually released (answer arrival), not the judged window,
            # so #24 keeps the frames before the defender was touched.
            self.interventions.append((self.newest, self.newest, "release_keys", "violation:" + ",".join(hits)))
            self.controller.release_keys()
            for e in self.events(actionable_only=True):
                if e["type"] in hits and e["windows"] >= self.p.stop_after and e["start_lo"] <= w.start <= e["end_hi"]:
                    self._stop("repeated_violation:" + e["type"], "violation:" + e["type"])
        elif w.answers and all(v == UNKNOWN for v in w.outcomes.values()):
            self.interventions.append((w.start, w.end, "review", "ambiguous"))
            self.controller.request_review({"start_frame": w.start, "end_frame": w.end, "answers": w.answers})

    def events(self, *, actionable_only: bool = False) -> list[dict]:
        """One event per run of overlapping/adjacent violation windows of the same type.

        Interval-censored: the violation starts in [start_lo, start_hi] and ends in [end_lo, end_hi].
        Diagnostic events retain every result; controller counts include only answers admitted to act.
        """
        out = []
        for kind in VIOLATIONS:
            ws = sorted((w for w in self.windows if w.outcomes.get(kind) == OBSERVED
                         and (not actionable_only or w.actionable)), key=lambda w: w.start)
            for s, e in merge([(w.start, w.end) for w in ws]):
                group = [w for w in ws if s <= w.start <= e]
                out.append({"type": kind, "start_lo": s, "start_hi": group[0].end,
                            "end_lo": group[-1].start, "end_hi": e, "windows": len(group)})
        return out

    def coverage(self) -> dict:
        judged = [w for w in self.windows if w.answers]
        # only fresh windows with at least one definite answer count; all-unknown windows prove nothing
        covered = merge([(w.start, w.end) for w in judged
                         if not w.stale and any(v in (CLEAR, OBSERVED) for v in w.outcomes.values())])
        uncovered, cursor = [], 0
        for s, e in covered:
            if s > cursor:
                uncovered.append((cursor, s - 1))
            cursor = e + 1
        if self.newest >= cursor:
            uncovered.append((cursor, self.newest))
        return {"due": len(self.windows) + len(self.skipped), "judged": len(judged),
                "skipped": len(self.skipped), "stale": sum(w.stale for w in self.windows),
                "failed": sum(bool(w.error) for w in self.windows),
                "pending": sum(not t.done() for t in self.in_flight), "stride": self.stride,
                "covered": covered, "uncovered": uncovered}
