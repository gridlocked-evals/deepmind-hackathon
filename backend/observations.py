"""Hydrogen extraction over immutable, permitted capture pixels (issue #5)."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, ValidationError, model_validator

from .capture import atomic_json, canonical
from .contracts import Contract, Identifier, ImageRegion, ModelTrace, Observation, ObservationBatch, Probability, TimeInterval
from .evidence_media import prepare_evidence
from .gateway import GatewayError
from .gateway.contracts import CallContext, Service

def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


PROMPT_ID = "hydrogen-observations-v1"
CONFIG_ID = "observations-config-v2"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{1,95}\Z")


class ObservationRequest(Contract):
    captureId: Identifier
    window: TimeInterval | None = None
    crop: ImageRegion | None = None
    maxFrames: Annotated[int, Field(ge=1, le=121)] = 32
    includeEvents: bool = False
    ownedInterval: TimeInterval | None = None

    @model_validator(mode="after")
    def explicit_event_ownership(self):
        if self.includeEvents != (self.ownedInterval is not None):
            raise ValueError("Event extraction requires an explicit ownedInterval; static extraction has none")
        return self


class FrameReference(Contract):
    frame: Annotated[int, Field(ge=0, le=120)]
    region: ImageRegion | None


class ExtractedObservation(Contract):
    category: Literal["anchor", "lane", "crossing", "count", "signal", "obstruction", "event"]
    feature: Literal["vehicle", "pedestrian", "cyclist", "traffic_signal", "crossing_markings", "lane_markings", "building", "road_surface", "barrier", "occluder", "other"]
    state: Literal["visible", "partially_occluded", "occluded", "unknown", "red", "amber", "green", "unlit"]
    countLower: Annotated[int, Field(ge=0, le=1000)] | None
    countUpper: Annotated[int, Field(ge=0, le=1000)] | None
    confidence: Probability | None
    unknownReason: Annotated[str, Field(min_length=1, max_length=400)] | None
    provenance: Literal["observed", "derived"]
    evidence: Annotated[list[FrameReference], Field(min_length=1, max_length=8)]
    eventType: Literal["crossing", "entry", "exit", "signal_change"] | None

    @model_validator(mode="after")
    def meaningful_fields(self):
        if (self.countLower is None) != (self.countUpper is None):
            raise ValueError("Count bounds must both be known or unknown")
        if self.countLower is not None and self.countLower > self.countUpper:
            raise ValueError("Count interval is reversed")
        if self.category != "count" and self.countLower is not None:
            raise ValueError("Only count observations contain count bounds")
        if self.category == "count" and len(self.evidence) != 1:
            raise ValueError("Counts are instantaneous per-frame counts, never accumulated flow")
        if self.category == "count" and self.countLower is None and not self.unknownReason:
            raise ValueError("Unknown counts require a reason")
        if self.state in {"unknown", "occluded"}:
            if not self.unknownReason:
                raise ValueError("Unobservable states require an explanation")
            if self.countLower is not None:
                raise ValueError("Unobservable counts must abstain rather than supply positive count bounds")
        required_feature = {"signal": "traffic_signal", "lane": "lane_markings", "crossing": "crossing_markings"}.get(self.category)
        if required_feature and self.feature != required_feature:
            raise ValueError("Observation category and visible feature disagree")
        if self.state in {"red", "amber", "green", "unlit"} and (self.category != "signal" or self.feature != "traffic_signal"):
            raise ValueError("Signal states require a traffic signal observation")
        if (self.category == "event") != (self.eventType is not None):
            raise ValueError("Temporal event type is exclusive to event observations")
        return self


class ExtractionOutput(Contract):
    observations: Annotated[list[ExtractedObservation], Field(min_length=1, max_length=16)]
    unknowns: Annotated[list[Annotated[str, Field(min_length=1, max_length=400)]], Field(max_length=16)]


class ObservationError(Exception):
    def __init__(self, code: str, status: int, attempt_id: str | None = None):
        self.code, self.status, self.attempt_id = code, status, attempt_id
        super().__init__(code)

    def public(self):
        return {"code": self.code, "attemptId": self.attempt_id,
                "message": "Observation extraction did not produce an accepted evidence artifact."}


class ObservationService:
    def __init__(self, captures, gateway, root: str | Path, *, evidence_preparer=prepare_evidence,
                 max_attempts: int = 1000, concurrency: int = 2):
        if not 1 <= concurrency <= 4 or not 1 <= max_attempts <= 10_000:
            raise ValueError("Invalid observation service limits")
        self.captures, self.gateway, self.root = captures, gateway, Path(root)
        self.prepare = evidence_preparer
        self.max_attempts = max_attempts
        self.lock = asyncio.Lock()
        self.slots = asyncio.Semaphore(concurrency)
        for category in ("attempts", "artifacts"):
            (self.root / category).mkdir(parents=True, exist_ok=True)
        self.corrupt_attempts = False
        self._recover_interrupted_attempts()

    def _recover_interrupted_attempts(self):
        """Single-process restart recovery; immutable accepted output wins over a torn final ledger write."""
        accepted = {}
        for path in (self.root / "artifacts").glob("*.json"):
            try:
                envelope = self._envelope(path.stem)
                artifact = ObservationBatch.model_validate(envelope["artifact"])
                accepted[envelope["attemptId"]] = artifact
            except (ObservationError, ValidationError, ValueError, KeyError, TypeError):
                # Corrupt artifacts cannot authorize events or be served by get().
                continue
        for path in (self.root / "attempts").glob("*.json"):
            try:
                record = self.attempt(path.stem)
            except ObservationError:
                # Unknown ownership cannot safely authorize a new event attempt.
                self.corrupt_attempts = True
                continue
            if record["status"] not in {"preparing", "running"}:
                continue
            artifact = accepted.get(record["attemptId"])
            if artifact is not None:
                record.update(status="accepted", artifactId=artifact.id, trace=artifact.trace.model_dump(),
                              completedAt=artifact.createdAt, error=None, recoveredAt=utc_now())
            else:
                record.update(status="interrupted", completedAt=utc_now(), error={"code": "interrupted"}, recoveredAt=utc_now())
            self._record(record)

    def _path(self, category: str, identifier: str) -> Path:
        if not _ID.fullmatch(identifier):
            raise ObservationError("invalid_id", 422)
        return self.root / category / f"{identifier}.json"

    def _record(self, record: dict):
        atomic_json(self._path("attempts", record["attemptId"]), record)

    def attempt(self, attempt_id: str) -> dict:
        path = self._path("attempts", attempt_id)
        if not path.is_file():
            raise ObservationError("not_found", 404)
        try:
            record = json.loads(path.read_text())
            if (record["attemptId"] != attempt_id or record["status"] not in
                    {"preparing", "running", "accepted", "rejected", "cancelled", "interrupted"}):
                raise ValueError()
            request = ObservationRequest.model_validate(record["request"])
            if record["captureId"] != request.captureId:
                raise ValueError()
            return record
        except (OSError, ValueError, KeyError, TypeError):
            raise ObservationError("integrity", 409) from None

    def _envelope(self, observation_id: str) -> dict:
        path = self._path("artifacts", observation_id)
        if not path.is_file():
            raise ObservationError("not_found", 404)
        try:
            value = json.loads(path.read_text())
            if not isinstance(value, dict) or set(value) != {"artifact", "attemptId", "evidence", "sha256"}:
                raise ValueError()
            content = {key: item for key, item in value.items() if key != "sha256"}
            if (hashlib.sha256(canonical(content)).hexdigest() != value["sha256"]
                    or not _ID.fullmatch(value["attemptId"])
                    or value["artifact"]["id"] != observation_id):
                raise ValueError()
            return value
        except (OSError, ValueError, KeyError, TypeError):
            raise ObservationError("integrity", 409) from None

    def get(self, observation_id: str) -> ObservationBatch:
        try:
            value = ObservationBatch.model_validate(self._envelope(observation_id)["artifact"])
        except ValidationError:
            raise ObservationError("integrity", 409) from None
        if not self._permitted_context(value.capture, self.captures.capture_context(value.capture.captureId)):
            raise ObservationError("capture_integrity", 409)
        return value

    @staticmethod
    def _permitted_context(subset, original):
        return (subset.model_dump(exclude={"allowedInterval"}) == original.model_dump(exclude={"allowedInterval"})
                and original.allowedInterval.startSeconds <= subset.allowedInterval.startSeconds
                and subset.allowedInterval.endSeconds <= original.allowedInterval.endSeconds)

    def evidence(self, observation_id: str) -> dict:
        self.get(observation_id)
        envelope = self._envelope(observation_id)
        return {"attemptId": envelope["attemptId"], "evidence": envelope["evidence"]}

    async def _claim_events(self, record: dict, capture):
        owned = record["request"]["ownedInterval"]
        window = record["request"]["window"] or capture.allowedInterval.model_dump()
        if owned is None:
            return
        if (capture.modality != "video" or not window["startSeconds"] <= owned["startSeconds"] < owned["endSeconds"] <= window["endSeconds"]):
            raise ObservationError("invalid_event_interval", 422)
        async with self.lock:
            for path in (self.root / "attempts").glob("*.json"):
                previous = self.attempt(path.stem)
                other = previous["request"].get("ownedInterval")
                if (previous["attemptId"] != record["attemptId"] and previous["captureId"] == record["captureId"]
                        and previous["status"] in {"running", "accepted"} and other
                        and max(owned["startSeconds"], other["startSeconds"]) < min(owned["endSeconds"], other["endSeconds"])):
                    raise ObservationError("event_interval_owned", 409)
            record["status"] = "running"
            self._record(record)

    @staticmethod
    def _prompt(prepared, request: ObservationRequest) -> dict:
        instructions = (
            "Inspect only the attached camera pixels. Prioritize up to eight useful observations (hard maximum sixteen); "
            "do not repeat the same static fact for every frame. Use brief unknown explanations and one or two evidence frames per fact. "
            "Return evidence-linked visible anchors, lane/crossing markings, "
            "instantaneous visible actor count intervals, signal states and obstructions. Every evidence.frame is the zero-based "
            "supplied frame number shown immediately before an image; do not invent frame numbers or timestamps. Evidence regions "
            "are normalized x,y,width,height in THAT PROVIDED image, including a supplied crop. Never infer metric lane widths, "
            "identity through occlusion, safety, trajectories, speed, traffic demand, flow rates, or disappearance/existence from occlusion. "
            "Occluded/unreadable signals and unidentifiable counts must abstain: state unknown, null bounds/confidence as appropriate, "
            "and a specific unknownReason. Count bounds refer to visible actors in exactly one frame and never sum over time. "
            "Use only structured feature/state enums; no free-form positive claims. Provenance observed means directly visible; derived "
            "means an inference from supplied pixels. Camera clock uncertainty and sparse sampling remain unknown, not precise motion evidence. "
        )
        if request.includeEvents:
            instructions += ("Temporal events are permitted only when at least two distinct supplied frames establish the event. "
                             f"Its entire evidence time interval must lie in the owned half-open interval {request.ownedInterval.model_dump()}; "
                             "omit events crossing its boundary. Do not count a hidden actor's entry/exit based on occlusion. ")
        else:
            instructions += "This is static evidence extraction. Do not emit temporal events, flow, motion or trajectories. "
        content = [{"type": "text", "text": instructions + "\nSampling audit: " + json.dumps(prepared.metadata())}]
        for index, frame in enumerate(prepared.frames):
            content.append({"type": "text", "text": f"Supplied frame {index}; source-relative time {frame.time_seconds:.6f} seconds."})
            content.append({"type": "image", "mime_type": frame.mime_type, "data": base64.b64encode(frame.data).decode("ascii")})
        return {"input": content}

    @staticmethod
    def _build(extracted: ExtractionOutput, prepared, request, trace, artifact_id: str) -> ObservationBatch:
        items = []
        used = set()
        for item in extracted.observations:
            if any(ref.frame >= len(prepared.frames) for ref in item.evidence):
                raise ObservationError("unknown_evidence_frame", 502)
            frames = [prepared.frames[ref.frame] for ref in item.evidence]
            refs = [frame.to_evidence_ref(ref.region) for frame, ref in zip(frames, item.evidence)]
            times = [frame.time_seconds for frame in frames]
            if item.category == "event":
                owned = request.ownedInterval
                if (not request.includeEvents or prepared.capture.modality != "video" or len(set(times)) < 2
                        or not owned.startSeconds <= min(times) < max(times) < owned.endSeconds):
                    raise ObservationError("unowned_temporal_event", 502)
            unknown = item.state in {"unknown", "occluded"} or (item.category == "count" and item.countLower is None)
            value = None if unknown else f"{item.feature}: {item.state}"
            if value is not None and item.category == "count":
                value += f"; instantaneous visible count [{item.countLower}, {item.countUpper}]"
            if value is not None and item.category == "event":
                value += f"; {item.eventType} between source times {min(times):.6f} and {max(times):.6f} seconds"
            identity = {"captureId": prepared.capture.captureId, "category": item.category, "value": value,
                        "evidence": [ref.model_dump() for ref in refs]}
            identifier = "ob_" + hashlib.sha256(canonical(identity)).hexdigest()[:32]
            if identifier in used:
                continue
            used.add(identifier)
            items.append(Observation(id=identifier, category=item.category, value=value, confidence=item.confidence,
                                     unknownReason=item.unknownReason, provenance=item.provenance, evidence=refs))
        return ObservationBatch(schemaVersion="1.0", kind="ObservationBatch", id=artifact_id, capture=prepared.capture,
                                createdAt=utc_now(), provenance="derived", trace=trace,
                                unknowns=extracted.unknowns + ["Sparse frame evidence does not establish trajectories, calibrated flow, or physical safety."],
                                observations=items)

    async def create(self, request: ObservationRequest) -> ObservationBatch:
        if self.corrupt_attempts:
            raise ObservationError("integrity", 409)
        attempt_id, artifact_id = f"attempt_{uuid4().hex}", f"observations_{uuid4().hex}"
        record = {"attemptId": attempt_id, "artifactId": None, "captureId": request.captureId,
                  "request": request.model_dump(), "status": "preparing", "startedAt": utc_now(), "completedAt": None,
                  "trace": None, "error": None}
        async with self.lock:
            if sum(1 for _ in (self.root / "attempts").glob("*.json")) >= self.max_attempts:
                raise ObservationError("attempt_storage_budget", 507)
            self._record(record)
        acquired = False
        try:
            try:
                await asyncio.wait_for(self.slots.acquire(), timeout=.05)
                acquired = True
            except TimeoutError:
                raise ObservationError("busy", 503) from None
            capture = self.captures.capture_context(request.captureId)
            self.captures.pin(request.captureId, attempt_id)
            prepared = await self.prepare(self.captures, request.captureId, window=request.window, crop=request.crop,
                                          max_frames=request.maxFrames, max_payload_bytes=8 * 1024 * 1024)
            if not self._permitted_context(prepared.capture, capture):
                raise ObservationError("capture_integrity", 409)
            await self._claim_events(record, capture)
            record.update(status="running", evidence=prepared.metadata())
            self._record(record)
            context = CallContext(episode_id=capture.captureId, prompt_id=PROMPT_ID,
                                  source_id=capture.sourceId, capture_id=capture.captureId,
                                  original_media_sha256=(capture.originalMediaSha256,))
            result = await self.gateway.generate(Service.HYDROGEN, stage="observation",
                                                 input_value=self._prompt(prepared, request)["input"],
                                                 context=context, response_model=ExtractionOutput, qualify=False)
            record["trace"] = self.gateway.trace(result, context, config_id=CONFIG_ID)
            extracted = ExtractionOutput.model_validate(result.output)
            artifact = self._build(extracted, prepared, request, ModelTrace.model_validate(record["trace"]), artifact_id)
            envelope = {"artifact": artifact.model_dump(), "attemptId": attempt_id, "evidence": prepared.metadata()}
            envelope["sha256"] = hashlib.sha256(canonical(envelope)).hexdigest()
            atomic_json(self._path("artifacts", artifact_id), envelope)
            self.gateway.accept_artifact(result, artifact_id=artifact_id, artifact_sha256=envelope["sha256"])
            record.update(status="accepted", artifactId=artifact_id, completedAt=utc_now())
            self._record(record)
            return artifact
        except asyncio.CancelledError:
            record.update(status="cancelled", completedAt=utc_now(), error={"code": "cancelled"})
            self._record(record)
            raise
        except Exception as exc:
            if isinstance(exc, ObservationError):
                error = exc
            elif isinstance(exc, GatewayError):
                error = ObservationError("model_" + exc.code.value, 503)
                record["gatewayRequestId"] = exc.request_id
            elif isinstance(exc, HTTPException):
                error = ObservationError("evidence_unavailable", exc.status_code)
            elif isinstance(exc, (ValidationError, ValueError, TypeError, KeyError, IndexError)):
                error = ObservationError("invalid_model_output", 502)
            else:
                error = ObservationError("observation_failed", 500)
            error.attempt_id = attempt_id
            record.update(status="rejected", completedAt=utc_now(), error={"code": error.code})
            self._record(record)
            raise error from None
        finally:
            if acquired:
                self.slots.release()


router = APIRouter(prefix="/api/observations", tags=["observations"])


@router.post("", status_code=201)
async def create_observation(body: ObservationRequest, request: Request, response: Response):
    try:
        artifact = await request.app.state.observations.create(body)
        envelope = request.app.state.observations._envelope(artifact.id)
        response.headers["X-Observation-Attempt-Id"] = envelope["attemptId"]
        return artifact
    except ObservationError as error:
        raise HTTPException(error.status, error.public()) from None


@router.get("/attempts/{attempt_id}")
async def get_attempt(attempt_id: str, request: Request):
    try:
        return request.app.state.observations.attempt(attempt_id)
    except ObservationError as error:
        raise HTTPException(error.status, error.public()) from None


@router.get("/{observation_id}/evidence")
async def get_evidence(observation_id: str, request: Request):
    try:
        return request.app.state.observations.evidence(observation_id)
    except ObservationError as error:
        raise HTTPException(error.status, error.public()) from None


@router.get("/{observation_id}")
async def get_observation(observation_id: str, request: Request):
    try:
        return request.app.state.observations.get(observation_id)
    except ObservationError as error:
        raise HTTPException(error.status, error.public()) from None
