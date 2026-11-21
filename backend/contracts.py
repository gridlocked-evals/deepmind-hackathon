"""Versioned Element 119 wire contracts. Image coordinates are normalized, not metric.

Run ``python scripts/export_contracts.py`` after changing these canonical models.
Semantic validators are mirrored in src/contracts/index.ts and exercised by shared fixtures.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{1,95}$")]
ProviderIdentifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Timestamp = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")]
Probability = Annotated[float, Field(ge=0, le=1)]
ModelId = Literal["gemini-4-hydrogen", "gemini-4-carbon", "gemini-nano-banana-2.1", "gemini-decision-1", "genie-4"]
Provenance = Literal["observed", "derived", "assumed", "inserted", "generated"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_timestamps(self):
        for name, value in self.__dict__.items():
            if name.endswith("At") and isinstance(value, str):
                datetime.fromisoformat(value.replace("Z", "+00:00"))
        return self


class ImageRegion(Contract):
    x: Probability
    y: Probability
    width: Annotated[float, Field(gt=0, le=1)]
    height: Annotated[float, Field(gt=0, le=1)]

    @model_validator(mode="after")
    def inside_image(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("Image region extends outside normalized image bounds")
        return self


class TimeInterval(Contract):
    startSeconds: Annotated[float, Field(ge=0, le=120)]
    endSeconds: Annotated[float, Field(ge=0, le=120)]

    @model_validator(mode="after")
    def ordered(self):
        if self.endSeconds < self.startSeconds:
            raise ValueError("Time interval ends before it starts")
        return self


class CaptureContext(Contract):
    captureId: Identifier
    sourceId: Identifier
    originalMediaSha256: Sha256
    modality: Literal["snapshot", "video"]
    durationSeconds: Annotated[float, Field(ge=0, le=120)]
    allowedInterval: TimeInterval
    receivedAt: Timestamp
    sourceCapturedAt: Timestamp | None
    sourceClock: Literal["exposure", "program_date_time", "unknown"]
    clockUncertaintySeconds: Annotated[float, Field(ge=0)] | None

    @model_validator(mode="after")
    def bounded_source(self):
        if self.allowedInterval.endSeconds > self.durationSeconds:
            raise ValueError("Allowed evidence interval exceeds source duration")
        if self.modality == "snapshot" and self.durationSeconds != 0:
            raise ValueError("A snapshot has zero temporal duration")
        if self.modality == "video" and (self.durationSeconds <= 0 or self.allowedInterval.endSeconds <= self.allowedInterval.startSeconds):
            raise ValueError("Video requires a positive permitted interval")
        if self.sourceClock == "unknown" and (self.sourceCapturedAt is not None or self.clockUncertaintySeconds is not None):
            raise ValueError("Unknown source clock cannot claim exposure time or measured clock uncertainty")
        if self.sourceClock != "unknown" and self.sourceCapturedAt is None:
            raise ValueError("Known source clock requires a source timestamp")
        return self


class EvidenceRef(Contract):
    captureId: Identifier
    originalMediaSha256: Sha256
    timeSeconds: Annotated[float, Field(ge=0, le=120)]
    frameIndex: Annotated[int, Field(ge=0)] | None
    region: ImageRegion | None


class ModelTrace(Contract):
    traceId: Identifier
    requestId: ProviderIdentifier
    providerRequestId: ProviderIdentifier | None
    modelId: ModelId
    promptId: Identifier
    configId: Identifier
    inputSha256: Sha256
    startedAt: Timestamp
    completedAt: Timestamp
    outcome: Literal["success", "error", "unknown"]
    inputTokens: Annotated[int, Field(ge=0)] | None
    outputTokens: Annotated[int, Field(ge=0)] | None

    @model_validator(mode="after")
    def chronological(self):
        if datetime.fromisoformat(self.completedAt) < datetime.fromisoformat(self.startedAt):
            raise ValueError("Model trace completed before it started")
        return self


def evidence_refs(value):
    if isinstance(value, EvidenceRef):
        yield value
    elif isinstance(value, BaseModel):
        for child in value.__dict__.values():
            yield from evidence_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from evidence_refs(child)


class Artifact(Contract):
    schemaVersion: Literal["1.0"]
    id: Identifier
    capture: CaptureContext
    createdAt: Timestamp
    provenance: Provenance
    trace: ModelTrace | None
    unknowns: list[str]

    @model_validator(mode="after")
    def permitted_evidence(self):
        capture = self.capture
        for ref in evidence_refs(self):
            if ref.captureId != capture.captureId or ref.originalMediaSha256 != capture.originalMediaSha256:
                raise ValueError("Evidence refers to another capture or original media hash")
            inside = (capture.allowedInterval.startSeconds <= ref.timeSeconds < capture.allowedInterval.endSeconds
                      if capture.modality == "video" else ref.timeSeconds == 0)
            if not inside:
                raise ValueError("Evidence time lies outside permitted source interval")
            if capture.modality == "snapshot" and ref.frameIndex not in (None, 0):
                raise ValueError("Snapshot evidence cannot reference subsequent frames")
        return self

    def require_model(self, model: str):
        if self.trace is None or self.trace.modelId != model or self.trace.outcome != "success":
            raise ValueError(f"Artifact requires a successful {model} trace")


class Observation(Contract):
    id: Identifier
    category: Literal["anchor", "lane", "crossing", "count", "signal", "obstruction", "event"]
    value: str | None
    confidence: Probability | None
    unknownReason: str | None
    provenance: Literal["observed", "derived", "assumed"]
    evidence: Annotated[list[EvidenceRef], Field(min_length=1)]

    @model_validator(mode="after")
    def explicit_unknown(self):
        if self.value is None and not self.unknownReason:
            raise ValueError("Unknown observations require an explanation")
        return self


class ObservationBatch(Artifact):
    kind: Literal["ObservationBatch"]
    provenance: Literal["observed", "derived"]
    observations: Annotated[list[Observation], Field(min_length=1)]

    @model_validator(mode="after")
    def observation_semantics(self):
        self.require_model("gemini-4-hydrogen")
        if len({item.id for item in self.observations}) != len(self.observations):
            raise ValueError("Observation IDs must be unique")
        if self.capture.modality == "snapshot" and any(item.category == "event" for item in self.observations):
            raise ValueError("Snapshot observations cannot claim temporal events")
        return self


class VehiclePermission(Contract):
    actor: Literal["car", "bus", "bicycle", "pedestrian", "emergency"]
    access: Literal["allow", "deny", "unknown"]


class EditedRegion(Contract):
    id: Identifier
    region: ImageRegion
    permissions: Annotated[list[VehiclePermission], Field(min_length=1)]
    provenance: Literal["inserted", "generated"]
    evidence: Annotated[list[EvidenceRef], Field(min_length=1)]

    @model_validator(mode="after")
    def consistent_permissions(self):
        if len({p.actor for p in self.permissions}) != len(self.permissions):
            raise ValueError("Each actor has exactly one permission per region; conflicting or duplicate permissions are invalid")
        return self


class StreetEdit(Artifact):
    kind: Literal["StreetEdit"]
    provenance: Literal["inserted"]
    observationBatchId: Identifier
    requestedChange: Annotated[str, Field(min_length=1)]
    status: Literal["accepted", "rejected", "needs_clarification"]
    regions: list[EditedRegion]
    preservedObservationIds: list[Identifier]
    nanoBananaPrompt: str | None
    worldPrompt: str | None
    explanation: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def edit_semantics(self):
        self.require_model("gemini-4-carbon")
        if len({item.id for item in self.regions}) != len(self.regions):
            raise ValueError("Edited region IDs must be unique")
        if self.status == "accepted" and (not self.regions or not self.nanoBananaPrompt or not self.worldPrompt):
            raise ValueError("Accepted edits require regions, image instructions, and a world prompt")
        return self


class ReferenceImage(Artifact):
    kind: Literal["ReferenceImage"]
    provenance: Literal["generated"]
    streetEditId: Identifier
    artifactId: Identifier
    mediaSha256: Sha256
    mediaType: Literal["image/png", "image/jpeg"]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def reference_semantics(self):
        self.require_model("gemini-nano-banana-2.1")
        if self.width * 9 != self.height * 16:
            raise ValueError("Reference image must have a 16:9 aspect ratio")
        if self.provenance != "generated":
            raise ValueError("Reference image must retain generated provenance")
        return self


class GateCheck(Contract):
    name: Identifier
    result: Literal["pass", "fail", "unknown"]
    probability: Probability | None
    confidence: Probability | None
    threshold: Probability
    minimumConfidence: Probability
    evidence: list[EvidenceRef]

    @model_validator(mode="after")
    def threshold_result(self):
        if self.probability is None or self.confidence is None or self.confidence < self.minimumConfidence:
            expected = "unknown"
        else:
            expected = "pass" if self.probability >= self.threshold else "fail"
        if self.result != expected:
            raise ValueError("Gate check result contradicts probability/confidence thresholds")
        return self


class GateDecision(Artifact):
    kind: Literal["GateDecision"]
    provenance: Literal["derived"]
    referenceImageId: Identifier
    verdict: Literal["accept", "reject", "unknown"]
    action: Literal["allow_world_creation", "block", "review"]
    checks: Annotated[list[GateCheck], Field(min_length=1)]

    @model_validator(mode="after")
    def operational_gate(self):
        self.require_model("gemini-decision-1")
        results = {item.result for item in self.checks}
        verdict = "reject" if "fail" in results else "unknown" if "unknown" in results else "accept"
        if self.verdict != verdict or self.action != {"accept": "allow_world_creation", "reject": "block", "unknown": "review"}[verdict]:
            raise ValueError("Gate verdict/action contradict its checks; unknown checks cannot authorize a world")
        return self


class WorldSession(Artifact):
    kind: Literal["WorldSession"]
    provenance: Literal["generated"]
    referenceImageId: Identifier
    gateDecisionId: Identifier
    interactionId: ProviderIdentifier | None
    state: Literal["created", "connecting", "active", "stopping", "completed", "failed"]
    transport: Literal["webrtc", "websocket"]
    replaySemantics: Literal["stochastic_no_exact_reset"]
    maximumDurationSeconds: Annotated[float, Field(gt=0, le=120)]
    maximumCostUsd: Annotated[float, Field(gt=0)]
    terminationReason: str | None

    @model_validator(mode="after")
    def session_semantics(self):
        if self.trace is not None and self.trace.modelId != "genie-4":
            raise ValueError("World session trace must identify Genie")
        if self.state in ("active", "stopping", "completed") and self.interactionId is None:
            raise ValueError("Connected session requires its provider interaction ID")
        if self.state in ("completed", "failed") and not self.terminationReason:
            raise ValueError("Terminal session requires an explicit termination reason")
        return self


class NavigationEvent(Artifact):
    kind: Literal["NavigationEvent"]
    provenance: Literal["observed", "derived"]
    worldSessionId: Identifier
    sequence: Annotated[int, Field(ge=0)]
    sessionTimeSeconds: Annotated[float, Field(ge=0, le=120)]
    frameSha256: Sha256
    controller: Literal["human", "hydrogen", "supervisor"]
    action: Literal["forward", "backward", "left", "right", "release_all", "terminate"]
    holdMilliseconds: Annotated[int, Field(ge=0, le=1000)]
    reason: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def bounded_control(self):
        if self.controller == "hydrogen":
            self.require_model("gemini-4-hydrogen")
        if self.action in ("release_all", "terminate") and self.holdMilliseconds != 0:
            raise ValueError("Release/termination commands cannot hold movement keys")
        return self


class EvaluationMetric(Contract):
    name: Identifier
    value: float | None
    unit: Annotated[str, Field(min_length=1)]
    unknownReason: str | None

    @model_validator(mode="after")
    def explicit_unknown(self):
        if self.value is None and not self.unknownReason:
            raise ValueError("Unknown metrics require an explanation")
        return self


class EvaluationResult(Artifact):
    kind: Literal["EvaluationResult"]
    provenance: Literal["derived"]
    worldSessionId: Identifier | None
    datasetId: Identifier
    split: Literal["development", "held_out"]
    baselineId: Identifier
    attemptIds: Annotated[list[Identifier], Field(min_length=1)]
    outcome: Literal["pass", "fail", "unknown"]
    evaluatedSeconds: Annotated[float, Field(ge=0)]
    unknownSeconds: Annotated[float, Field(ge=0)]
    metrics: list[EvaluationMetric]

    @model_validator(mode="after")
    def evaluation_semantics(self):
        if self.unknownSeconds > self.evaluatedSeconds:
            raise ValueError("Unknown coverage exceeds evaluated duration")
        if len(set(self.attemptIds)) != len(self.attemptIds):
            raise ValueError("Evaluation attempt IDs must be unique")
        return self


class VerticalSlice(Contract):
    schemaVersion: Literal["1.0"]
    observations: ObservationBatch
    edit: StreetEdit
    reference: ReferenceImage
    gate: GateDecision
    session: WorldSession
    navigation: list[NavigationEvent]
    evaluation: EvaluationResult

    @model_validator(mode="after")
    def consistent_links(self):
        artifacts = [self.observations, self.edit, self.reference, self.gate, self.session, *self.navigation, self.evaluation]
        if any(item.capture != self.observations.capture for item in artifacts):
            raise ValueError("Vertical slice artifacts must retain the same immutable capture context")
        if len({item.id for item in artifacts}) != len(artifacts):
            raise ValueError("Artifact IDs must be unique")
        links = [(self.edit.observationBatchId, self.observations.id), (self.reference.streetEditId, self.edit.id),
                 (self.gate.referenceImageId, self.reference.id), (self.session.referenceImageId, self.reference.id),
                 (self.session.gateDecisionId, self.gate.id)]
        links += [(event.worldSessionId, self.session.id) for event in self.navigation]
        if self.evaluation.worldSessionId is not None:
            links.append((self.evaluation.worldSessionId, self.session.id))
        if any(actual != expected for actual, expected in links):
            raise ValueError("Artifact references do not match the vertical slice")
        if self.edit.status != "accepted" or self.gate.verdict != "accept":
            raise ValueError("World creation requires an accepted edit and image gate")
        known = {item.id for item in self.observations.observations}
        if not set(self.edit.preservedObservationIds) <= known:
            raise ValueError("Preserved observations must exist in the observation batch")
        if len({event.sequence for event in self.navigation}) != len(self.navigation):
            raise ValueError("Navigation sequence numbers must be unique")
        if any(event.sessionTimeSeconds > self.session.maximumDurationSeconds for event in self.navigation):
            raise ValueError("Navigation event exceeds the world session time budget")
        return self
