"""Shared evaluation primitives for #11, #13 and #24.

Every timestamp in evaluation code is a Genie frame index from the recorded
stream (#9), never wall-clock time.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Awaitable, Protocol

from backend.gateway.contracts import CallContext, CallResult

GENIE_FPS = 24


@dataclass(frozen=True)
class RecordedFrame:
    """One received Genie frame as saved to disk by the session recorder (#9)."""

    session_id: str
    index: int  # 0-based position in the received stream; the shared clock
    sha256: str
    path: str


def frames_to_seconds(frames: int, fps: int = GENIE_FPS) -> float:
    return frames / fps


def seconds_to_frames(seconds: float, fps: int = GENIE_FPS) -> int:
    return math.ceil(seconds * fps)


class Judge(Protocol):
    """Signature of ModelGateway.judge, so tests can pass a fake."""

    def __call__(self, *, stage: str, input_value: Any, questions: dict,
                 context: CallContext) -> Awaitable[CallResult]: ...


def yes_probability(answer: dict) -> float:
    """Probability of 'true' from a validated Decisions `noul` answer."""
    if answer.get("type") != "noul":
        raise ValueError("expected a noul answer")
    return float(answer["noul"])


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion; (0, 1) when there are no trials."""
    if not 0 <= successes <= trials:
        raise ValueError("need 0 <= successes <= trials")
    if trials == 0:
        return (0.0, 1.0)
    p = successes / trials
    denom = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))
