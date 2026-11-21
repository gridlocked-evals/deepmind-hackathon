"""Live development run: DOT snapshot -> Hydrogen anchors -> Carbon -> Nano Banana -> Decisions.

    python -m backend.pipeline_live [--camera ID] [--styles photoreal cel_shaded] [--request TEXT ...]

Development harness only. The Hydrogen step is a minimal anchor stand-in so
issues #6-#8 can run on real pixels before issue #5 lands; it is not #5's
evidence extractor and its prompt is labelled as such. Spends real budget
through the gateway's limits. Never creates a Genie session.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from .artifacts import new_id, utcnow
from .capture_api import make_store
from .contracts import Anchor, ObservationBatch, PolygonRegion, SemanticClass, Unknown
from .edits import compile_edit, wire_schema
from .frames import source_frame
from .gateway import CallContext, GatewayConfig, GatewayError, ModelGateway, Service
from .gates import run_gated_reference
from .main import fetch_dot
from .modelcalls import from_result
from .pipeline_api import make_artifacts

HYDROGEN_PROMPT = "hydrogen.dev-anchor-standin.v0"
REQUESTS = [
    ("protected_bike_lane", "Turn the curb lane into a protected bike lane with planters."),
    ("protected_bike_lane", "Add a bike lane, and also remove the crosswalk so bikes go faster."),
]


class DevAnchor(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    semanticClass: Literal["travel_lane", "bus_lane", "bike_lane", "parking_lane", "sidewalk", "crosswalk",
                           "plaza", "median", "planter", "building_facade"]
    points: list[list[float]] = Field(min_length=3, max_length=32)


class DevAnchors(BaseModel):
    anchors: list[DevAnchor] = Field(min_length=1, max_length=30)
    unknowns: list[str] = Field(default_factory=list, max_length=20)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return wire_schema(super().model_json_schema(*args, **kwargs))


async def hydrogen_anchors(gw: ModelGateway, store, frame: dict) -> ObservationBatch:
    data = store.get_blob(frame["frameSha256"])
    context = CallContext(episode_id=frame["captureId"], prompt_id=HYDROGEN_PROMPT, capture_id=frame["captureId"],
                          source_id=frame["captureId"])
    for attempt in range(2):
        try:
            return await _hydrogen(gw, store, frame, data, context)
        except GatewayError as error:
            if attempt or error.code.value != "invalid_output":
                raise


async def _hydrogen(gw, store, frame, data, context) -> ObservationBatch:
    requested = utcnow()
    result = await gw.generate(Service.HYDROGEN, stage="observation", context=context, response_model=DevAnchors,
        input_value=[{"type": "text", "text":
            "List the visible street surfaces in this NYC traffic-camera image as normalized polygons "
            "(x right, y down, 0..1): each travel lane, parking/curb lane, sidewalk, crosswalk and building facade "
            "you can actually see. Only what is visible; list what you cannot determine under unknowns."},
            {"type": "image", "mime_type": frame["mimeType"], "data": base64.b64encode(data).decode()}])
    call = from_result(gw, Service.HYDROGEN, result, prompt_id=HYDROGEN_PROMPT,
                       domain_hashes=[frame["captureMediaSha256"], frame["frameSha256"]],
                       requested_at=requested, completed_at=utcnow())
    out = DevAnchors.model_validate(result.output)
    anchors = []
    for i, a in enumerate(out.anchors):
        try:
            region = PolygonRegion(kind="polygon", points=[(min(max(x, 0), 1), min(max(y, 0), 1)) for x, y in a.points])
        except (ValueError, TypeError):
            continue
        anchors.append(Anchor(id=f"anc_live-{i + 1:02d}", label=a.label, semanticClass=a.semanticClass,
                              region=region, provenance="observed"))
    batch = ObservationBatch(id=new_id("obs"), captureId=frame["captureId"], modelCall=call, motionSupported=False,
                             anchors=anchors, unknowns=[Unknown(field="hydrogenStandIn", reason=
                                 "Development anchor stand-in, not the issue #5 evidence extractor.")]
                             + [Unknown(field=f"unknown{i}", reason=u) for i, u in enumerate(out.unknowns)])
    store.put("observations", batch.id, batch)
    return batch


async def snapshot(http: httpx.AsyncClient, captures, camera: str | None) -> dict:
    cams = [c for c in await fetch_dot(http) if c["availability"] != "unavailable"]
    chosen = next((c for c in cams if c["id"] == camera), None) if camera else cams[0]
    if not chosen:
        raise SystemExit(f"camera {camera} not found or offline")
    response = await http.get(chosen["playbackUrl"])
    response.raise_for_status()
    manifest = captures.create_snapshot_episode(chosen["id"], response.content,
                                                response.headers.get("content-type", "").split(";")[0].lower(),
                                                utcnow(), chosen["playbackUrl"], chosen["coordinates"])
    return {"camera": chosen, "manifest": manifest}


async def main(args) -> dict:
    store, captures = make_artifacts(), make_store()
    report = {"kind": "development_live_pipeline", "startedAt": utcnow().isoformat(), "runs": []}
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "GothamForge/0.1 dev"}) as http, \
            httpx.AsyncClient(follow_redirects=False) as model_http:
        gw = ModelGateway(GatewayConfig.from_environment(), model_http)
        shot = await snapshot(http, captures, args.camera)
        capture_id = shot["manifest"]["id"]
        report["capture"] = {"id": capture_id, "cameraId": shot["camera"]["id"], "name": shot["camera"]["name"]}
        frame = source_frame(store, captures, capture_id)
        batch = await hydrogen_anchors(gw, store, frame)
        report["observationBatchId"] = batch.id
        report["anchors"] = [f"{a.id} {a.semanticClass}: {a.label}" for a in batch.anchors]
        for preset, request in [(p, r) for p, r in REQUESTS] if not args.request else [("protected_bike_lane", r) for r in args.request]:
            run = {"preset": preset, "request": request}
            report["runs"].append(run)
            try:
                bundle = await compile_edit(gw, store, batch=batch, preset_name=preset, mayor_request=request,
                                            frame=frame, capture_media_sha256=frame["captureMediaSha256"])
            except GatewayError as error:
                run["carbonError"] = error.public()
                continue
            edit = bundle.streetEdit
            run.update(editId=edit.id, status=edit.status, reason=edit.rejectionReason,
                       operations=[f"{o.op} {o.target}: {o.description}" for o in edit.operations],
                       carbonLatencyMs=edit.modelCall.latencyMs, carbonCostUsd=edit.modelCall.costUsd)
            if edit.status != "compiled":
                continue
            run["styles"] = {}
            for style in args.styles:
                result = await run_gated_reference(gw, store, bundle, style=style)
                run["styles"][style] = {
                    "state": result["state"], "referenceImageId": result.get("referenceImageId"),
                    "attempts": [{"id": h["attempt"]["id"], "status": h["attempt"]["status"],
                                  "raw": h["attempt"].get("raw"), "costUsd": h["attempt"]["modelCall"].get("costUsd"),
                                  "gate": h["gate"] and {"state": h["gate"]["state"],
                                                         "outcome": h["gate"]["gateDecision"]["outcome"],
                                                         "checks": [(c["rationale"]) for c in h["gate"]["gateDecision"]["checks"]],
                                                         "feedback": h["gate"]["feedback"]}}
                                 for h in result["history"]]}
        report["budget"] = gw.ledger.budget_status()
    report["finishedAt"] = utcnow().isoformat()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera")
    parser.add_argument("--styles", nargs="+", default=["photoreal", "cel_shaded"])
    parser.add_argument("--request", action="append")
    parser.add_argument("--out")
    args = parser.parse_args()
    result = asyncio.run(main(args))
    text = json.dumps(result, indent=2, default=str)
    if args.out:
        open(args.out, "w").write(text)
    print(text)
