"""Carbon street-edit compilation (issue #6).

Gemini 4 Carbon turns Hydrogen evidence plus a mayor request into a typed
``StreetEdit`` and the artifacts later stages need: preserved anchors, intended
lane use, unknown geometry, Nano Banana instructions and a Genie world prompt.
Ordinary code then validates the result. Carbon proposes; the validator decides.

Everything is image-space. Nothing here claims metric widths, route
feasibility or traffic safety; that needs calibrated geometry (#16).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .artifacts import ArtifactStore, new_id, sha256, utcnow
from .contracts import (EditOperation, ObservationBatch, Permissions, PolygonRegion, SemanticClass, StreetEdit,
                        Unknown)
from .gateway import CallContext, GatewayError, ModelGateway, Service
from .gateway.contracts import ErrorCode
from .modelcalls import from_result

PROMPT_ID = "carbon.street-edit.v1"
LaneUser = Literal["pedestrian", "cyclist", "bus", "general_traffic", "delivery", "emergency"]
METRIC_CLAIM = re.compile(r"\b\d+(\.\d+)?\s*(m|meters?|metres?|ft|feet|foot|cm|inch(es)?)\b", re.I)
SAFETY_CLAIM = re.compile(r"\b(safer|safe for|guarantees? safety|eliminates? (crashes|collisions)|feasible route)\b", re.I)
ALWAYS_FORBIDDEN: tuple[SemanticClass, ...] = ("building_facade", "sidewalk", "crosswalk", "pedestrian", "signal")


@dataclass(frozen=True)
class Preset:
    name: str
    title: str
    main: SemanticClass
    convertible: tuple[SemanticClass, ...]
    extras: tuple[SemanticClass, ...]
    must_allow: tuple[str, ...]
    must_forbid: tuple[str, ...]
    look: str

    @property
    def allowed(self) -> list[SemanticClass]:
        return list(dict.fromkeys((self.main, *self.convertible, *self.extras)))

    def spec(self) -> dict:
        return {"preset": self.name, "title": self.title, "resultClass": self.main,
                "mayConvert": list(self.convertible), "mayAdd": [self.main, *self.extras],
                "laneUseMustAllow": list(self.must_allow), "laneUseMustForbid": list(self.must_forbid),
                "neverTouch": list(ALWAYS_FORBIDDEN), "look": self.look}


PRESETS = {p.name: p for p in (
    Preset("protected_bike_lane", "Protected bike lane", "bike_lane", ("parking_lane", "travel_lane"),
           ("planter", "median"), ("cyclist",), ("general_traffic",),
           "green-painted bike lane separated from traffic by planters or a raised buffer"),
    Preset("busway", "Busway", "bus_lane", ("travel_lane", "parking_lane"), ("median",),
           ("bus",), ("general_traffic",), "red-painted bus-only lane with BUS ONLY markings"),
    Preset("plaza", "Pedestrian plaza", "plaza", ("travel_lane", "parking_lane"), ("planter",),
           ("pedestrian",), ("general_traffic", "bus"), "car-free paved plaza with planters and seating"),
)}


# ---------------------------------------------------------------- Carbon output schema

class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CarbonOperation(_Out):
    op: Literal["add", "remove", "modify", "restyle"]
    target: SemanticClass = Field(description="Class of the existing surface changed, or of the new surface added")
    becomes: SemanticClass | None = Field(default=None, description="For modify: what the surface becomes")
    points: list[list[float]] = Field(min_length=3, max_length=16,
                                      description="Normalized image polygon [[x,y],...], x right, y down, 0..1")
    anchorIds: list[str] = Field(min_length=1)
    evidenceObservationIds: list[str] = Field(default_factory=list)
    description: str = Field(min_length=1, max_length=400)


class CarbonLaneUse(_Out):
    allowed: list[LaneUser] = Field(min_length=1)
    forbidden: list[LaneUser] = Field(default_factory=list)


class CarbonUnknown(_Out):
    field: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=400)


class CarbonEditOutput(_Out):
    status: Literal["compiled", "rejected", "needs_clarification"]
    reason: str = Field(max_length=600, description="Required. For rejected: why. For needs_clarification: the"
                        " single clarifying question. For compiled: one sentence summarizing the edit.")
    operations: list[CarbonOperation] = Field(default_factory=list, max_length=8)
    laneUse: CarbonLaneUse | None = None
    preservedAnchorIds: list[str] = Field(default_factory=list)
    unknownGeometry: list[CarbonUnknown] = Field(default_factory=list, max_length=12)
    imageInstructions: str | None = Field(default=None, max_length=2000)
    worldPrompt: str | None = Field(default=None, max_length=1200)


_WIRE_DROP = {"maxLength", "minLength", "maxItems", "minItems", "description", "title", "default",
              "minimum", "maximum"}


def wire_schema(value, in_properties: bool = False):
    """Schema sent to the provider. Length/range bounds make the served grammar too large
    ("too many states"), so they are dropped here and enforced by full Pydantic validation
    of the response instead. Property names are never dropped."""
    if isinstance(value, dict):
        return {k: wire_schema(v, k == "properties") for k, v in value.items() if in_properties or k not in _WIRE_DROP}
    if isinstance(value, list):
        return [wire_schema(v) for v in value]
    return value


class CarbonWireOutput(CarbonEditOutput):
    """Same validation as CarbonEditOutput; advertises the simplified wire schema."""

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return wire_schema(CarbonEditOutput.model_json_schema())


class EditBundle(BaseModel):
    """Everything the image and world stages need, without rereading evidence or labels."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    streetEdit: StreetEdit
    preset: str
    captureId: str
    captureMediaSha256: str | None
    sourceFrame: dict | None
    anchorLabels: dict[str, str] = Field(default_factory=dict)
    crossingAnchorIds: list[str] = Field(default_factory=list)
    operationResults: dict[str, SemanticClass | None] = Field(default_factory=dict)
    laneUse: CarbonLaneUse | None
    preservedAnchorIds: list[str]
    imageInstructions: str | None
    worldPrompt: str | None
    evidenceObservationIds: list[str]
    validationErrors: list[str]
    carbonOutput: dict | None
    carbonResultHash: str
    gatewayRequestId: str


class EditRequestError(ValueError):
    pass


# ---------------------------------------------------------------- validation

def _bbox(region) -> tuple[float, float, float, float]:
    if region.kind == "box":
        return region.x, region.y, region.x + region.w, region.y + region.h
    xs, ys = [p[0] for p in region.points], [p[1] for p in region.points]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap_fraction(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter = max(0, min(ax1, bx1) - max(ax0, bx0)) * max(0, min(ay1, by1) - max(ay0, by0))
    area = (ax1 - ax0) * (ay1 - ay0)
    return inter / area if area > 0 else 0.0


def request_permissions(preset: Preset, allowed: list[str] | None, forbidden: list[str] | None) -> Permissions:
    """Preset scope narrowed by the caller. Contradictions are rejected before Carbon is called."""
    scope = preset.allowed if allowed is None else list(dict.fromkeys(allowed))
    outside = [c for c in scope if c not in preset.allowed]
    if outside:
        raise EditRequestError(f"{', '.join(outside)} is outside the {preset.name} preset")
    blocked = list(dict.fromkeys([*ALWAYS_FORBIDDEN, *(forbidden or [])]))
    try:
        permissions = Permissions(allowed=scope, forbidden=blocked)
    except ValidationError as exc:
        raise EditRequestError(exc.errors()[0]["msg"].removeprefix("Value error, ")) from None
    if preset.main not in permissions.allowed or preset.main in permissions.forbidden:
        raise EditRequestError(f"the {preset.name} preset must allow its result class {preset.main}")
    return permissions


def validate_output(out: CarbonEditOutput, batch: ObservationBatch, preset: Preset,
                    permissions: Permissions) -> list[str]:
    errors: list[str] = []
    anchors = {a.id: a for a in batch.anchors}
    observations = {o.id for o in batch.observations}
    texts = [out.imageInstructions or "", out.worldPrompt or "", *(o.description for o in out.operations)]
    if out.status != "compiled":
        if not (out.reason or "").strip():
            errors.append("a rejected or unclear edit must state a reason")
        return errors
    if not out.operations:
        errors.append("a compiled edit needs at least one operation")
    if not (out.imageInstructions or "").strip() or not (out.worldPrompt or "").strip():
        errors.append("a compiled edit needs image instructions and a world prompt")
    for i, op in enumerate(out.operations):
        name = f"operation {i + 1}"
        if op.target in permissions.forbidden or op.target not in permissions.allowed:
            errors.append(f"{name} targets {op.target}, which this request does not allow")
        if op.op == "modify" and op.becomes not in (preset.main, *preset.extras):
            errors.append(f"{name} must turn {op.target} into {preset.main}, not {op.becomes}")
        if op.op == "modify" and (op.becomes not in permissions.allowed or op.becomes in permissions.forbidden):
            errors.append(f"{name} creates {op.becomes}, which this request does not allow")
        if op.op == "add" and op.target not in (preset.main, *preset.extras):
            errors.append(f"{name} adds {op.target}, which the {preset.name} preset cannot add")
        if op.op == "remove" and op.target not in preset.convertible:
            errors.append(f"{name} removes {op.target}, which the {preset.name} preset cannot remove")
        missing = [a for a in op.anchorIds if a not in anchors]
        if missing:
            errors.append(f"{name} references unknown anchors {missing}")
        bad_evidence = [o for o in op.evidenceObservationIds if o not in observations]
        if bad_evidence:
            errors.append(f"{name} cites unknown observations {bad_evidence}")
        try:
            region = PolygonRegion(kind="polygon", points=[tuple(p) for p in op.points])
        except (ValidationError, TypeError, ValueError):
            errors.append(f"{name} has an invalid image region")
            continue
        surfaces = [anchors[a] for a in op.anchorIds if a in anchors
                    and anchors[a].semanticClass in (*preset.convertible, preset.main, *preset.extras)]
        if not surfaces:
            errors.append(f"{name} is not anchored to an editable street surface")
        elif max(_overlap_fraction(_bbox(region), _bbox(a.region)) for a in surfaces) < 0.25:
            errors.append(f"{name} region is unsupported: it does not overlap its anchored surface")
        touched = [a for a in op.anchorIds if a in out.preservedAnchorIds and op.op in ("remove", "modify")]
        if touched:
            errors.append(f"{name} changes preserved anchors {touched}")
    if out.operations and not any(
            (op.op == "add" and op.target == preset.main) or (op.op == "modify" and op.becomes == preset.main)
            for op in out.operations):
        errors.append(f"the edit never creates a {preset.main}")
    unknown_preserved = [a for a in out.preservedAnchorIds if a not in anchors]
    if unknown_preserved:
        errors.append(f"preserved anchors {unknown_preserved} are not in the evidence")
    crossings = [a.id for a in batch.anchors if a.semanticClass == "crosswalk" and a.id not in out.preservedAnchorIds]
    if crossings:
        errors.append(f"observed crossings {crossings} must be preserved")
    lane = out.laneUse
    if lane is None:
        errors.append("a compiled edit must state intended lane use")
    else:
        both = sorted(set(lane.allowed) & set(lane.forbidden))
        if both:
            errors.append(f"contradictory lane use: {', '.join(both)} both allowed and forbidden")
        if not set(preset.must_allow) <= set(lane.allowed):
            errors.append(f"{preset.title} must allow {', '.join(preset.must_allow)}")
        if not set(preset.must_forbid) <= set(lane.forbidden):
            errors.append(f"{preset.title} must forbid {', '.join(preset.must_forbid)}")
    for text in texts:
        if METRIC_CLAIM.search(text):
            errors.append("output states metric dimensions, which image-space edits cannot support")
            break
    for text in texts:
        if SAFETY_CLAIM.search(text):
            errors.append("output claims safety or route feasibility, which this stage cannot establish")
            break
    return errors


# ---------------------------------------------------------------- prompting

SYSTEM = """You are the street-edit compiler for an NYC street-redesign demo.
Translate the mayor's request into image-space edit operations over ONE camera frame.

Rules:
- Use only anchors and observations listed in EVIDENCE. Every operation cites at least one anchor ID.
- Regions are polygons in normalized image coordinates (x right, y down, 0..1) on the referenced frame.
- Stay inside the PRESET: only convert/add the classes it lists; never touch its neverTouch classes.
- Preserve every observed crosswalk and every landmark the new layout does not replace; list them in preservedAnchorIds.
- Observed surfaces stay as observed; new surfaces are generated. Put anything you cannot see
  (hidden curb lines, occluded lane ends, true widths) in unknownGeometry instead of guessing.
- Never state metric widths/distances, safety outcomes, or route feasibility. This is a visual proposal.
- If the request is ambiguous, contradictory, or outside the preset, return status "needs_clarification"
  or "rejected" with no operations. "reason" is always required: the clarifying question or the rejection reason.
- imageInstructions: concrete edit instructions for an image model that must keep the camera viewpoint,
  buildings and crossings unchanged. worldPrompt: a short first-person description of the edited street.
Return JSON only, matching the schema."""


def build_input(batch: ObservationBatch, preset: Preset, permissions: Permissions, request: str,
                frame: tuple[str, bytes] | None) -> list[dict]:
    evidence = {"captureId": batch.captureId, "motionSupported": batch.motionSupported,
                "anchors": [a.model_dump(mode="json") for a in batch.anchors],
                "observations": [o.model_dump(mode="json") for o in batch.observations],
                "unknowns": [u.model_dump(mode="json") for u in batch.unknowns]}
    text = "\n\n".join([SYSTEM, "PRESET:\n" + json.dumps(preset.spec()),
                        "PERMISSIONS:\n" + permissions.model_dump_json(),
                        "EVIDENCE:\n" + json.dumps(evidence), "MAYOR REQUEST:\n" + request])
    parts = [{"type": "text", "text": text}]
    if frame:
        parts.append({"type": "image", "mime_type": frame[0], "data": base64.b64encode(frame[1]).decode()})
    return parts


# ---------------------------------------------------------------- compile

async def compile_edit(gateway: ModelGateway, store: ArtifactStore, *, batch: ObservationBatch, preset_name: str,
                       mayor_request: str, allowed: list[str] | None = None, forbidden: list[str] | None = None,
                       frame: dict | None = None, capture_media_sha256: str | None = None,
                       purpose: str = "development") -> EditBundle:
    """Call Carbon once and persist a validated (or visibly rejected) edit bundle.

    Raises EditRequestError for requests rejected before any model call and
    GatewayError when Carbon itself fails; neither produces a StreetEdit.
    """
    preset = PRESETS.get(preset_name)
    if preset is None:
        raise EditRequestError(f"unknown preset {preset_name}; supported: {', '.join(PRESETS)}")
    request = (mayor_request or "").strip()
    if not 1 <= len(request) <= 2000:
        raise EditRequestError("the mayor request must be 1-2000 characters")
    permissions = request_permissions(preset, allowed, forbidden)
    frame_bytes = (frame["mimeType"], store.get_blob(frame["frameSha256"])) if frame else None
    input_value = build_input(batch, preset, permissions, request, frame_bytes)
    batch_hash = sha256(batch.model_dump_json().encode())
    domain_hashes = [batch_hash, *( [capture_media_sha256] if capture_media_sha256 else []),
                     *([frame["frameSha256"]] if frame else [])]
    context = CallContext(episode_id=batch.captureId, prompt_id=PROMPT_ID, capture_id=batch.captureId,
                          source_id=batch.captureId, purpose=purpose)
    requested = utcnow()
    result = await gateway.generate(Service.CARBON, stage="compilation", input_value=input_value,
                                    context=context, response_model=CarbonWireOutput)
    call = from_result(gateway, Service.CARBON, result, prompt_id=PROMPT_ID, domain_hashes=domain_hashes,
                       requested_at=requested, completed_at=utcnow())
    try:
        out = CarbonEditOutput.model_validate(result.output)
    except ValidationError:
        raise GatewayError(ErrorCode.INVALID_OUTPUT, request_id=result.request_id) from None
    errors = validate_output(out, batch, preset, permissions)
    edit_id = new_id("edit")
    unknowns = [Unknown(field="metricGeometry",
                        reason="Image-space edit from an uncalibrated camera; widths and route feasibility are not measured.")]
    unknowns += [Unknown(field=u.field, reason=u.reason) for u in out.unknownGeometry]
    ops: list[EditOperation] = []
    results: dict[str, SemanticClass | None] = {}
    status, reason = out.status, out.reason
    if not errors and status == "compiled":
        for i, op in enumerate(out.operations):
            op_id = f"op_{edit_id.removeprefix('edit_')}-{i + 1}"
            ops.append(EditOperation(id=op_id, op=op.op, target=op.target, anchorIds=op.anchorIds,
                                     description=op.description,
                                     region=PolygonRegion(kind="polygon", points=[tuple(p) for p in op.points])))
            results[op_id] = op.becomes if op.op == "modify" else op.target if op.op == "add" else None
    elif errors:
        status, reason = "rejected", "validation: " + "; ".join(errors)
    try:
        edit = StreetEdit(id=edit_id, observationBatchId=batch.id, mayorRequest=request, modelCall=call,
                          status=status, permissions=permissions, operations=ops,
                          rejectionReason=reason, unknowns=unknowns)
    except ValidationError as exc:
        edit = StreetEdit(id=edit_id, observationBatchId=batch.id, mayorRequest=request, modelCall=call,
                          status="rejected", permissions=permissions, operations=[], unknowns=unknowns,
                          rejectionReason="validation: " + exc.errors()[0]["msg"])
        errors.append(edit.rejectionReason)
    compiled = edit.status == "compiled"
    bundle = EditBundle(
        streetEdit=edit, preset=preset.name, captureId=batch.captureId, captureMediaSha256=capture_media_sha256,
        sourceFrame=frame, operationResults=results,
        anchorLabels={a.id: f"{a.label} ({a.semanticClass})" for a in batch.anchors},
        crossingAnchorIds=[a.id for a in batch.anchors if a.semanticClass == "crosswalk"], laneUse=out.laneUse if compiled else None,
        preservedAnchorIds=out.preservedAnchorIds if compiled else [],
        imageInstructions=out.imageInstructions if compiled else None,
        worldPrompt=out.worldPrompt if compiled else None,
        evidenceObservationIds=sorted({o for op in out.operations for o in op.evidenceObservationIds}) if compiled else [],
        validationErrors=errors, carbonOutput=out.model_dump(mode="json"),
        carbonResultHash="sha256:" + result.result_hash, gatewayRequestId=result.request_id)
    store.put("edits", edit_id, bundle)
    return bundle
