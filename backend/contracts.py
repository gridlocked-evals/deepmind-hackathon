"""Element 119 shared contracts, schema version ``element119.v1``.

These Pydantic models are the backend source of truth for the submission
pipeline: capture -> Hydrogen observations -> Carbon street edit -> Nano Banana
reference -> Decisions gate -> Genie world session -> navigation -> evaluation.
``src/contracts/element119.ts`` mirrors them for the frontend, and both sides
validate ``contracts/fixtures/vertical_slice.v1.json`` in their test suites.

Conventions (see docs/CONTRACTS.md):
- Image regions are normalized to the image they reference: x right, y down,
  both in [0, 1]. No metric geometry is implied in this milestone.
- ``mediaTimeS`` values are seconds from the first frame of the captured media.
  ``sessionTimeS`` values are seconds since the Genie session started streaming.
  Wall-clock timestamps are UTC ISO-8601 and record when *we* did something
  (retrieval, request, response), never the camera's exposure time.
- Genie sessions are stochastic. They carry no seed or branch ID and never
  claim deterministic replay; those fields belong to the later simulator
  milestone and are rejected here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "element119.v1"
MAX_EVIDENCE_SECONDS = 120.0

Sha256 = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
CameraId = Annotated[str, Field(pattern=r"^[A-Za-z0-9-]{1,100}$")]
UnitFloat = Annotated[float, Field(ge=0.0, le=1.0)]


def _id(prefix: str):
    return Annotated[str, Field(pattern=rf"^{prefix}_[A-Za-z0-9-]{{4,64}}$")]


CaptureId = _id("cap")
ObservationBatchId = _id("obs")
ObservationId = _id("ob")
AnchorId = _id("anc")
StreetEditId = _id("edit")
OperationId = _id("op")
ReferenceImageId = _id("ref")
GateDecisionId = _id("gate")
WorldSessionId = _id("world")
NavigationEventId = _id("nav")
EvaluationResultId = _id("eval")
ModelCallId = _id("call")

Provenance = Literal["observed", "derived", "assumed", "inserted", "generated"]
Service = Literal["hydrogen", "carbon", "nano_banana", "decisions", "genie"]
SemanticClass = Literal[
    "travel_lane", "bus_lane", "bike_lane", "parking_lane", "sidewalk", "crosswalk",
    "plaza", "median", "planter", "signal", "signage", "vehicle", "pedestrian",
    "cyclist", "building_facade", "other",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Unknown(Strict):
    """A field or quantity we deliberately did not guess."""
    field: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ModelCall(Strict):
    """Provenance for one inference request. Secrets never appear here."""
    id: ModelCallId
    service: Service
    modelId: str = Field(min_length=1)
    promptId: str = Field(min_length=1)
    configId: str = Field(min_length=1)
    requestId: str | None = None
    inputHashes: list[Sha256] = Field(min_length=1)
    requestedAt: datetime
    completedAt: datetime | None = None
    latencyMs: float | None = Field(default=None, ge=0)
    inputTokens: int | None = Field(default=None, ge=0)
    outputTokens: int | None = Field(default=None, ge=0)
    costUsd: float | None = Field(default=None, ge=0)
    outcome: Literal["ok", "timeout", "quota", "invalid_output", "error", "blocked"]

    @model_validator(mode="after")
    def _order(self):
        if self.completedAt and self.completedAt < self.requestedAt:
            raise ValueError("completedAt precedes requestedAt")
        return self


class BoxRegion(Strict):
    kind: Literal["box"]
    x: UnitFloat
    y: UnitFloat
    w: Annotated[float, Field(gt=0.0, le=1.0)]
    h: Annotated[float, Field(gt=0.0, le=1.0)]

    @model_validator(mode="after")
    def _inside(self):
        if self.x + self.w > 1.0 + 1e-9 or self.y + self.h > 1.0 + 1e-9:
            raise ValueError("box region extends outside the image")
        return self


class PolygonRegion(Strict):
    kind: Literal["polygon"]
    points: list[tuple[UnitFloat, UnitFloat]] = Field(min_length=3, max_length=64)

    @model_validator(mode="after")
    def _area(self):
        pts = self.points
        area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
        if abs(area) < 1e-9:
            raise ValueError("polygon region has zero area")
        return self


ImageRegion = Annotated[Union[BoxRegion, PolygonRegion], Field(discriminator="kind")]


class MediaTimeRange(Strict):
    """Seconds from the first frame of the captured media."""
    startS: float = Field(ge=0)
    endS: float = Field(ge=0)

    @model_validator(mode="after")
    def _order(self):
        if self.endS < self.startS:
            raise ValueError("time range ends before it starts")
        return self


class Capture(Strict):
    """Immutable reference to the original evidence. Recording is issue #4."""
    id: CaptureId
    cameraId: CameraId
    sourceKind: Literal["hls", "snapshot"]
    mediaSha256: Sha256
    mediaType: str = Field(min_length=1)
    durationS: float = Field(ge=0, le=MAX_EVIDENCE_SECONDS)
    frameCount: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    retrievalStartedAt: datetime
    retrievalEndedAt: datetime
    captureClock: Literal["unknown", "source_reported"]
    provenance: Literal["observed"] = "observed"
    unknowns: list[Unknown] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self):
        if self.retrievalEndedAt < self.retrievalStartedAt:
            raise ValueError("retrieval ends before it starts")
        if self.sourceKind == "snapshot" and self.durationS != 0:
            raise ValueError("snapshot captures have zero media duration")
        return self


class Anchor(Strict):
    id: AnchorId
    label: str = Field(min_length=1)
    semanticClass: SemanticClass
    region: ImageRegion
    provenance: Literal["observed", "derived"]


class Observation(Strict):
    id: ObservationId
    kind: Literal["actor", "signal_state", "lane_use", "queue", "movement", "obstruction", "scene"]
    semanticClass: SemanticClass
    label: str = Field(min_length=1)
    region: ImageRegion
    time: MediaTimeRange
    confidence: UnitFloat
    provenance: Literal["observed", "derived"]


class ObservationBatch(Strict):
    """Hydrogen's evidence for one capture (issue #5)."""
    schemaVersion: Literal["element119.v1"] = SCHEMA_VERSION
    id: ObservationBatchId
    captureId: CaptureId
    modelCall: ModelCall
    motionSupported: bool
    anchors: list[Anchor] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    unknowns: list[Unknown] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self):
        if self.modelCall.service != "hydrogen":
            raise ValueError("observation batches must come from Hydrogen")
        if not self.motionSupported and any(o.kind == "movement" for o in self.observations):
            raise ValueError("movement observations require motion-supporting evidence")
        _unique([a.id for a in self.anchors] + [o.id for o in self.observations])
        return self


class Permissions(Strict):
    allowed: list[SemanticClass] = Field(min_length=1)
    forbidden: list[SemanticClass] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self):
        both = sorted(set(self.allowed) & set(self.forbidden))
        if both:
            raise ValueError(f"contradictory permissions: {', '.join(both)} both allowed and forbidden")
        return self


class EditOperation(Strict):
    id: OperationId
    op: Literal["add", "remove", "modify", "restyle"]
    target: SemanticClass
    region: ImageRegion
    anchorIds: list[AnchorId] = Field(default_factory=list)
    description: str = Field(min_length=1)
    provenance: Literal["inserted"] = "inserted"


class StreetEdit(Strict):
    """Carbon's compiled mayor request (issue #6)."""
    schemaVersion: Literal["element119.v1"] = SCHEMA_VERSION
    id: StreetEditId
    observationBatchId: ObservationBatchId
    mayorRequest: str = Field(min_length=1, max_length=2000)
    modelCall: ModelCall
    status: Literal["compiled", "rejected", "needs_clarification"]
    permissions: Permissions
    operations: list[EditOperation] = Field(default_factory=list)
    rejectionReason: str | None = None
    unknowns: list[Unknown] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self):
        if self.modelCall.service != "carbon":
            raise ValueError("street edits must be compiled by Carbon")
        if self.status == "compiled" and not self.operations:
            raise ValueError("a compiled edit needs at least one operation")
        if self.status != "compiled" and not self.rejectionReason:
            raise ValueError("rejected or unclear edits must state a reason")
        for op in self.operations:
            if op.target in self.permissions.forbidden:
                raise ValueError(f"operation {op.id} targets forbidden class {op.target}")
            if op.target not in self.permissions.allowed:
                raise ValueError(f"operation {op.id} targets {op.target}, which is not allowed")
        _unique([op.id for op in self.operations])
        return self


class ReferenceImage(Strict):
    """Nano Banana 16:9 mayor reference (issue #7)."""
    schemaVersion: Literal["element119.v1"] = SCHEMA_VERSION
    id: ReferenceImageId
    captureId: CaptureId
    streetEditId: StreetEditId
    style: str = Field(min_length=1, max_length=200)
    attempt: int = Field(ge=1)
    modelCall: ModelCall
    imageSha256: Sha256
    width: int = Field(ge=16)
    height: int = Field(ge=9)
    provenance: Literal["generated"] = "generated"

    @model_validator(mode="after")
    def _check(self):
        if self.modelCall.service != "nano_banana":
            raise ValueError("reference images must come from Nano Banana")
        if self.width * 9 != self.height * 16:
            raise ValueError("reference images must be exactly 16:9")
        return self


class GateCheck(Strict):
    question: str = Field(min_length=1)
    answer: Literal["pass", "fail", "unknown"]
    score: UnitFloat | None = None
    rationale: str = Field(min_length=1)


GATE_EFFECTS = {
    "reference_image": {"accept": "allow_world_creation", "reject": "block_world_creation", "unknown": "block_world_creation"},
    "navigation_window": {"accept": "continue", "reject": "terminate_session", "unknown": "flag_unknown"},
}


class GateSubject(Strict):
    kind: Literal["reference_image", "navigation_window"]
    id: str = Field(min_length=1)
    sessionTime: tuple[float, float] | None = None


class GateDecision(Strict):
    """Operational Decisions judgment (issues #8 and #11)."""
    schemaVersion: Literal["element119.v1"] = SCHEMA_VERSION
    id: GateDecisionId
    subject: GateSubject
    modelCall: ModelCall
    checks: list[GateCheck] = Field(min_length=1)
    outcome: Literal["accept", "reject", "unknown"]
    effect: Literal["allow_world_creation", "block_world_creation", "continue", "terminate_session", "flag_unknown"]

    @model_validator(mode="after")
    def _check(self):
        if self.modelCall.service != "decisions":
            raise ValueError("gate decisions must come from the Decisions API")
        answers = {c.answer for c in self.checks}
        expected = "reject" if "fail" in answers else "unknown" if "unknown" in answers else "accept"
        if self.outcome != expected:
            raise ValueError(f"outcome {self.outcome} does not follow from checks (expected {expected})")
        if GATE_EFFECTS[self.subject.kind][self.outcome] != self.effect:
            raise ValueError(f"effect {self.effect} is inconsistent with a {self.outcome} {self.subject.kind} gate")
        return self


class SessionBudget(Strict):
    maxSeconds: float = Field(gt=0)
    maxUsd: float = Field(gt=0)
    spentSeconds: float = Field(ge=0)
    spentUsd: float = Field(ge=0)


class WorldSession(Strict):
    """A stochastic Genie session (issue #9). Not a replayable simulator run."""
    schemaVersion: Literal["element119.v1"] = SCHEMA_VERSION
    id: WorldSessionId
    genieSessionId: str | None = None
    referenceImageId: ReferenceImageId
    gateDecisionId: GateDecisionId
    modelCall: ModelCall
    status: Literal["requested", "streaming", "terminated", "failed"]
    startedAt: datetime | None = None
    terminatedAt: datetime | None = None
    terminationReason: Literal["user", "gate", "budget", "timeout", "completed", "error"] | None = None
    budget: SessionBudget
    deterministicReplay: Literal[False] = False

    @model_validator(mode="after")
    def _check(self):
        if self.modelCall.service != "genie":
            raise ValueError("world sessions must come from Genie")
        ended = self.status in ("terminated", "failed")
        if ended != (self.terminationReason is not None) or ended != (self.terminatedAt is not None):
            raise ValueError("terminated/failed sessions need terminatedAt and terminationReason; live ones must not have them")
        if self.startedAt and self.terminatedAt and self.terminatedAt < self.startedAt:
            raise ValueError("session terminates before it starts")
        return self


class NavigationEvent(Strict):
    """One control step in a world session (issue #10)."""
    schemaVersion: Literal["element119.v1"] = SCHEMA_VERSION
    id: NavigationEventId
    worldSessionId: WorldSessionId
    sequence: int = Field(ge=0)
    sessionTimeS: float = Field(ge=0)
    driver: Literal["human", "hydrogen"]
    action: Literal["forward", "back", "strafe_left", "strafe_right", "turn_left", "turn_right", "stop", "terminate"]
    durationS: float = Field(ge=0, le=10)
    frameSha256: Sha256 | None = None
    modelCall: ModelCall | None = None
    rationale: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.driver == "hydrogen" and (self.modelCall is None or self.modelCall.service != "hydrogen"):
            raise ValueError("Hydrogen-driven events need a Hydrogen model call")
        if self.driver == "human" and self.modelCall is not None:
            raise ValueError("human events carry no model call")
        return self


class Metric(Strict):
    name: str = Field(min_length=1)
    value: float | None
    unit: str
    n: int = Field(ge=0)
    unknownCount: int = Field(ge=0)


class EvaluationResult(Strict):
    """Aggregated outcome for one condition on a locked input set (issue #13)."""
    schemaVersion: Literal["element119.v1"] = SCHEMA_VERSION
    id: EvaluationResultId
    inputSetId: str = Field(min_length=1)
    inputSetSha256: Sha256
    condition: str = Field(min_length=1)
    isBaseline: bool
    captureIds: list[CaptureId] = Field(min_length=1)
    worldSessionIds: list[WorldSessionId] = Field(default_factory=list)
    attempts: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    unknown: int = Field(ge=0)
    metrics: list[Metric] = Field(default_factory=list)
    provenance: Literal["derived"] = "derived"
    notes: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.succeeded + self.failed + self.unknown != self.attempts:
            raise ValueError("succeeded + failed + unknown must equal attempts; unknown outcomes are never dropped")
        return self


class VerticalSlice(Strict):
    """One end-to-end episode, used as the shared integration fixture."""
    schemaVersion: Literal["element119.v1"] = SCHEMA_VERSION
    capture: Capture
    observationBatch: ObservationBatch
    streetEdit: StreetEdit
    referenceImage: ReferenceImage
    referenceGate: GateDecision
    worldSession: WorldSession
    navigationEvents: list[NavigationEvent] = Field(default_factory=list)
    navigationGates: list[GateDecision] = Field(default_factory=list)
    evaluation: EvaluationResult | None = None

    @model_validator(mode="after")
    def _links(self):
        cap, obs, edit, ref, gate, world = (self.capture, self.observationBatch, self.streetEdit,
                                            self.referenceImage, self.referenceGate, self.worldSession)
        if obs.captureId != cap.id or ref.captureId != cap.id:
            raise ValueError("observation batch and reference image must reference the capture")
        if cap.mediaSha256 not in obs.modelCall.inputHashes:
            raise ValueError("Hydrogen observations must hash the original capture media")
        for o in obs.observations:
            if o.time.endS > cap.durationS:
                raise ValueError(f"observation {o.id} time reference is outside the capture")
        if edit.observationBatchId != obs.id:
            raise ValueError("street edit must reference the observation batch")
        anchors = {a.id for a in obs.anchors}
        for op in edit.operations:
            missing = [a for a in op.anchorIds if a not in anchors]
            if missing:
                raise ValueError(f"operation {op.id} references unknown anchors {missing}")
        if ref.streetEditId != edit.id or edit.status != "compiled":
            raise ValueError("reference image must follow a compiled street edit")
        if gate.subject.kind != "reference_image" or gate.subject.id != ref.id:
            raise ValueError("reference gate must judge the reference image")
        if ref.imageSha256 not in gate.modelCall.inputHashes:
            raise ValueError("reference gate must hash the judged image")
        if world.referenceImageId != ref.id or world.gateDecisionId != gate.id:
            raise ValueError("world session must reference the gated image and its decision")
        if gate.effect != "allow_world_creation":
            raise ValueError("a world session requires an accepted reference gate")
        seqs = [e.sequence for e in self.navigationEvents]
        if seqs != sorted(set(seqs)):
            raise ValueError("navigation sequences must be unique and ordered")
        times = [e.sessionTimeS for e in self.navigationEvents]
        if times != sorted(times):
            raise ValueError("navigation session times must be non-decreasing")
        for e in self.navigationEvents:
            if e.worldSessionId != world.id:
                raise ValueError(f"navigation event {e.id} belongs to another session")
        for g in self.navigationGates:
            if g.subject.kind != "navigation_window" or g.subject.id != world.id:
                raise ValueError(f"navigation gate {g.id} must judge this world session")
        if self.evaluation and cap.id not in self.evaluation.captureIds:
            raise ValueError("evaluation must include this capture")
        return self


def _unique(ids: list[str]) -> None:
    seen = set()
    for item in ids:
        if item in seen:
            raise ValueError(f"duplicate id {item}")
        seen.add(item)


CONTRACTS = {
    "Capture": Capture, "ObservationBatch": ObservationBatch, "StreetEdit": StreetEdit,
    "ReferenceImage": ReferenceImage, "GateDecision": GateDecision, "WorldSession": WorldSession,
    "NavigationEvent": NavigationEvent, "EvaluationResult": EvaluationResult, "VerticalSlice": VerticalSlice,
}


def json_schema() -> dict:
    return {"schemaVersion": SCHEMA_VERSION,
            "contracts": {name: model.model_json_schema() for name, model in CONTRACTS.items()}}
