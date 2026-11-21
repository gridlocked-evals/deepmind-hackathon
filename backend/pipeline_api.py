"""Routes for Carbon edits (#6), Nano Banana references (#7) and reference gates (#8).

See docs/EDIT_REFERENCE_GATE.md.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError

from .artifacts import ArtifactError, ArtifactStore
from .capture import CaptureError
from .contracts import ObservationBatch
from .edits import PRESETS, EditBundle, EditRequestError, compile_edit
from .frames import source_frame
from .gateway import GatewayError
from .gateway.contracts import ErrorCode
from .gates import gate_reference, load_thresholds, run_gated_reference
from .references import GENIE_TRANSPORT, MAX_ATTEMPTS, STYLES, attempts, generate_reference

router = APIRouter(prefix="/api")
ROOT = Path(__file__).resolve().parent.parent


def make_artifacts() -> ArtifactStore:
    return ArtifactStore(Path(os.environ.get("PIPELINE_DIR", ROOT / "var" / "pipeline")))


_GATEWAY_STATUS = {ErrorCode.NOT_CONFIGURED: 503, ErrorCode.DISABLED: 503, ErrorCode.BUDGET: 402,
                   ErrorCode.QUOTA: 429, ErrorCode.TIMEOUT: 504, ErrorCode.CONCURRENCY: 429,
                   ErrorCode.INVALID_INPUT: 422}


def _fail(exc: Exception) -> JSONResponse:
    if isinstance(exc, ArtifactError):
        return JSONResponse(exc.detail, status_code=exc.status)
    if isinstance(exc, CaptureError):
        return JSONResponse(exc.detail, status_code=exc.status)
    if isinstance(exc, GatewayError):
        return JSONResponse({"error": "model_call_failed", **exc.public()},
                            status_code=_GATEWAY_STATUS.get(exc.code, 502))
    if isinstance(exc, EditRequestError):
        return JSONResponse({"error": str(exc)}, status_code=422)
    raise exc


def _bundle(store: ArtifactStore, edit_id: str) -> EditBundle:
    return EditBundle.model_validate(store.get("edits", edit_id))


@router.get("/edits/options")
async def options():
    return {"presets": {k: p.spec() for k, p in PRESETS.items()},
            "styles": {k: v["status"] for k, v in STYLES.items()},
            "maxReferenceAttempts": MAX_ATTEMPTS, "genieTransport": GENIE_TRANSPORT,
            "gateThresholds": {k: v for k, v in load_thresholds().items() if k != "questions"}}


@router.post("/observations")
async def register_observations(batch: dict, request: Request):
    """Store a Hydrogen ObservationBatch (#5 writes here; tests and fixtures may too)."""
    try:
        parsed = ObservationBatch.model_validate(batch)
    except ValidationError as exc:
        return JSONResponse({"error": "invalid observation batch",
                             "detail": exc.errors(include_url=False, include_context=False)},
                            status_code=422)
    try:
        return request.app.state.artifacts.put("observations", parsed.id, parsed)
    except ArtifactError as exc:
        return _fail(exc)


class EditBody(BaseModel):
    observationBatchId: str
    mayorRequest: str = Field(min_length=1, max_length=2000)
    preset: str = "protected_bike_lane"
    permissions: dict | None = None
    frameTimeS: float = Field(default=0.0, ge=0)


@router.post("/edits")
async def create_edit(body: EditBody, request: Request):
    store: ArtifactStore = request.app.state.artifacts
    try:
        batch = ObservationBatch.model_validate(store.get("observations", body.observationBatchId))
        frame, media_hash = None, None
        try:
            frame = source_frame(store, request.app.state.captures, batch.captureId, body.frameTimeS)
            media_hash = frame["captureMediaSha256"]
        except CaptureError:
            frame = None  # evidence without stored media (e.g. fixtures): no image stage later
        perms = body.permissions or {}
        bundle = await compile_edit(request.app.state.models, store, batch=batch, preset_name=body.preset,
                                    mayor_request=body.mayorRequest, allowed=perms.get("allowed"),
                                    forbidden=perms.get("forbidden"), frame=frame,
                                    capture_media_sha256=media_hash)
    except (ArtifactError, GatewayError, EditRequestError) as exc:
        return _fail(exc)
    return bundle.model_dump(mode="json")


@router.get("/edits/{edit_id}")
async def get_edit(edit_id: str, request: Request):
    try:
        return request.app.state.artifacts.get("edits", edit_id)
    except ArtifactError as exc:
        return _fail(exc)


class ReferenceBody(BaseModel):
    streetEditId: str
    style: str = "photoreal"
    allowExperimental: bool = False
    feedback: str | None = Field(default=None, max_length=600)


@router.post("/references")
async def create_reference(body: ReferenceBody, request: Request):
    store = request.app.state.artifacts
    try:
        record = await generate_reference(request.app.state.models, store, _bundle(store, body.streetEditId),
                                          style=body.style, feedback=body.feedback,
                                          allow_experimental=body.allowExperimental)
    except ArtifactError as exc:
        return _fail(exc)
    status = 200 if record.status == "generated" else 502
    return JSONResponse(record.model_dump(mode="json"), status_code=status)


@router.get("/edits/{edit_id}/references")
async def list_references(edit_id: str, request: Request):
    store = request.app.state.artifacts
    rows = attempts(store, edit_id)
    gates = {g["referenceImageId"]: g for g in store.list("gates") if g["streetEditId"] == edit_id}
    return {"streetEditId": edit_id, "attempts": rows, "gates": [gates[r["id"]] for r in rows if r["id"] in gates]}


@router.get("/references/{reference_id}")
async def get_reference(reference_id: str, request: Request):
    try:
        return request.app.state.artifacts.get("references", reference_id)
    except ArtifactError as exc:
        return _fail(exc)


@router.get("/references/{reference_id}/image")
async def reference_image(reference_id: str, request: Request, raw: bool = False):
    store = request.app.state.artifacts
    try:
        record = store.get("references", reference_id)
        if record["status"] != "generated":
            return JSONResponse({"error": "This attempt produced no usable image.", **(record["error"] or {})},
                                status_code=404)
        digest = record["raw"]["sha256"] if raw else record["referenceImage"]["imageSha256"]
        data = store.get_blob(digest)
    except ArtifactError as exc:
        return _fail(exc)
    media = "image/png" if not raw else {"PNG": "image/png", "JPEG": "image/jpeg"}.get(record["raw"]["format"])
    return Response(data, media_type=media, headers={"Cache-Control": "private, max-age=31536000, immutable",
                                                       "X-Content-Sha256": digest})


@router.get("/edits/{edit_id}/source-frame")
async def edit_source_frame(edit_id: str, request: Request):
    store = request.app.state.artifacts
    try:
        frame = _bundle(store, edit_id).sourceFrame
        if not frame:
            return JSONResponse({"error": "This edit has no stored source frame."}, status_code=404)
        data = store.get_blob(frame["frameSha256"])
    except ArtifactError as exc:
        return _fail(exc)
    return Response(data, media_type=frame["mimeType"], headers={"X-Content-Sha256": frame["frameSha256"]})


@router.post("/references/{reference_id}/gate")
async def gate(reference_id: str, request: Request):
    store = request.app.state.artifacts
    try:
        record = store.get("references", reference_id)
        result = await gate_reference(request.app.state.models, store, _bundle(store, record["streetEditId"]),
                                      reference_id)
    except ArtifactError as exc:
        return _fail(exc)
    return result.model_dump(mode="json")


class GatedBody(BaseModel):
    style: str = "photoreal"
    allowExperimental: bool = False


@router.post("/edits/{edit_id}/gated-reference")
async def gated_reference(edit_id: str, body: GatedBody, request: Request):
    """Generate, gate and (at most twice) re-edit. Returns every attempt."""
    store = request.app.state.artifacts
    try:
        return await run_gated_reference(request.app.state.models, store, _bundle(store, edit_id),
                                         style=body.style, allow_experimental=body.allowExperimental)
    except ArtifactError as exc:
        return _fail(exc)


@router.get("/gates/{gate_id}")
async def get_gate(gate_id: str, request: Request):
    try:
        return request.app.state.artifacts.get("gates", gate_id)
    except ArtifactError as exc:
        return _fail(exc)
