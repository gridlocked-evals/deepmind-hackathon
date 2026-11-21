"""Validated evidence tables for #5; shared ObservationBatch remains compatible."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import BoxRegion, CaptureId, CameraId, SemanticClass, Sha256, Unknown
from .edits import wire_schema

PROMPT_ID = "hydrogen.street-evidence.v3"
WINDOW_SECONDS = 8.0
OWNED_SECONDS = 6.0
MAX_FRAMES = 6
MAX_WINDOWS = 20
MAX_REINSPECTIONS = 2
ACTORS = {"vehicle", "pedestrian", "cyclist"}
EVENTS = {"entered_view", "exited_view", "crossing", "occluded", "visible_again"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, strict=True)


class Span(Strict):
    startS: float = Field(ge=0)
    endS: float = Field(ge=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.endS < self.startS:
            raise ValueError("The interval ends before it starts")
        return self


class ExtractRequest(Strict):
    captureId: CaptureId
    cameraId: CameraId
    startS: float = Field(default=0, ge=0)
    endS: float | None = Field(default=None, ge=0)


class ReinspectRequest(Strict):
    # Fixed questions prevent arbitrary instructions or new camera access.
    focus: Literal["counts", "signals", "anchors", "obstruction", "events"]
    time: Span
    roi: BoxRegion


class CountInterval(Strict):
    lower: int | None = Field(default=None, ge=0, le=10000, strict=True)
    upper: int | None = Field(default=None, ge=0, le=10000, strict=True)

    @model_validator(mode="after")
    def ordered(self):
        if self.upper is not None and (self.lower is None or self.upper < self.lower):
            raise ValueError("A count upper bound requires an ordered lower bound")
        return self


class Fact(Strict):
    kind: Literal["anchor", "lane", "crosswalk", "count", "signal", "obstruction", "event"]
    semanticClass: SemanticClass
    label: str = Field(min_length=1, max_length=240)
    region: BoxRegion
    time: Span
    frameIndices: list[int] = Field(min_length=1, max_length=MAX_FRAMES)
    visibility: Literal["visible", "partial", "unobservable"]
    state: Literal["unknown", "red", "amber", "green", "off", "blocked", "clear",
                   "entered_view", "exited_view", "crossing", "occluded", "visible_again"] = "unknown"
    count: CountInterval = Field(default_factory=CountInterval)
    confidence: float = Field(ge=0, le=1)
    provenance: Literal["observed", "derived"]

    @model_validator(mode="after")
    def semantics(self):
        if len(self.frameIndices) != len(set(self.frameIndices)) or any(type(i) is not int or i < 0 for i in self.frameIndices):
            raise ValueError("Frame references must be distinct nonnegative integers")
        if self.visibility == "unobservable" and (self.state != "unknown" or self.count.lower is not None):
            raise ValueError("Unobservable quantities must abstain")
        if self.kind != "count" and (self.count.lower is not None or self.count.upper is not None):
            raise ValueError("Only count facts may carry count bounds")
        if self.kind == "count" and (self.semanticClass not in ACTORS or self.state != "unknown"):
            raise ValueError("Counts describe visible actor populations only")
        if self.kind == "signal" and (self.semanticClass != "signal" or self.state not in {"unknown", "red", "amber", "green", "off"}):
            raise ValueError("Invalid signal state")
        if self.kind == "signal" and self.visibility != "visible" and self.state != "unknown":
            raise ValueError("Unreadable signals must remain unknown")
        if self.kind == "event" and (self.state not in EVENTS or self.provenance != "derived"):
            raise ValueError("Temporal events require an explicit derived event type")
        if self.kind == "obstruction" and self.state not in {"unknown", "blocked", "clear"}:
            raise ValueError("Invalid obstruction state")
        if self.kind in {"anchor", "lane", "crosswalk"} and self.state != "unknown":
            raise ValueError("Geometry is separate from dynamic state")
        return self


class HydrogenOutput(Strict):
    captureId: CaptureId
    sourceId: CameraId
    prefixSha256: Sha256
    facts: list[Fact] = Field(default_factory=list, max_length=24)
    unknowns: list[Unknown] = Field(default_factory=list, max_length=24)


class WireOutput(HydrogenOutput):
    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        def constants(value):
            if isinstance(value, list):
                return [constants(v) for v in value]
            if isinstance(value, dict):
                result = {k:constants(v) for k,v in value.items() if k != 'const'}
                if 'const' in value:
                    result['enum'] = [value['const']]
                return result
            return value
        # The shared simplified wire schema removes JSON Schema const. Preserve
        # its constraint as a supported singleton enum before simplifying.
        return wire_schema(constants(HydrogenOutput.model_json_schema()))


class Annotation(Strict):
    kind: Literal["anchor", "lane", "crosswalk", "count", "signal", "obstruction", "event"]
    semanticClass: SemanticClass
    time: Span
    region: BoxRegion
    frameIds: list[str] = Field(min_length=1, max_length=MAX_FRAMES)
    present: Literal["yes", "no", "unknown"]
    count: CountInterval = Field(default_factory=CountInterval)
    state: str | None = Field(default=None, max_length=32)
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def semantics(self):
        if len(self.frameIds) != len(set(self.frameIds)):
            raise ValueError("Annotation frames must be distinct")
        if self.kind != "count" and (self.count.lower is not None or self.count.upper is not None):
            raise ValueError("Only count annotations may contain count bounds")
        if self.kind == "count" and (self.semanticClass not in ACTORS or self.time.startS != self.time.endS):
            raise ValueError("Visible counts describe one original frame and an actor class")
        if self.present != "yes" and (self.count.lower is not None or self.state not in {None, "unknown"}):
            raise ValueError("Absent or unknown annotations cannot assert a count or state")
        if self.kind == "signal" and (self.semanticClass != "signal" or self.state not in {None, "unknown", "red", "amber", "green", "off"}):
            raise ValueError("Invalid annotated signal state")
        if self.kind == "event" and self.present == "yes" and (self.state not in EVENTS or self.time.startS >= self.time.endS):
            raise ValueError("Event annotations require a bounded interval and event type")
        if self.kind in {"anchor", "lane", "crosswalk", "count"} and self.state not in {None, "unknown"}:
            raise ValueError("Geometry and visible counts do not assert dynamic states")
        return self


class ManualReview(Strict):
    extractionId: str = Field(pattern=r"^extract_[A-Za-z0-9-]{4,64}$")
    captureId: CaptureId
    prefixSha256: Sha256
    extractionSha256: Sha256
    reviewer: str = Field(min_length=1, max_length=100)
    provenance: Literal["human_annotation"]
    reviewedOriginalPixels: Literal[True]
    reviewMode: Literal["independent_raw_pixels", "model_assisted"]
    annotations: list[Annotation] = Field(min_length=1, max_length=100)
