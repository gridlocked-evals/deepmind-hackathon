"""python -m backend.gateway.smoke [--live] [--services hydrogen carbon ...]."""
from __future__ import annotations

import argparse
import asyncio
import base64
from io import BytesIO
import json
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field, ValidationError

from .config import GatewayConfig
from .contracts import CallContext, GatewayError, Service, canonical
from .service import ModelGateway


class SmokeObservation(BaseModel):
    visible_vehicle_count: int = Field(ge=0, le=10)
    description: str = Field(min_length=10)


class SmokeEdit(BaseModel):
    requested_change: str = Field(min_length=10)
    preserved_features: list[str] = Field(min_length=1)


def fixture_image() -> dict:
    """Small synthetic image to verify multimodal transport; never benchmark data."""
    image = Image.new("RGB", (320, 180), "#e4e4df")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 55, 320, 135), fill="#555555")
    draw.line((0, 95, 320, 95), fill="white", width=2)
    draw.rectangle((60, 65, 105, 85), fill="#1764d9")
    draw.rectangle((190, 105, 235, 125), fill="#d93324")
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return {"type": "image", "mime_type": "image/jpeg", "data": base64.b64encode(buffer.getvalue()).decode()}


async def run_smoke(gateway: ModelGateway, services: list[Service], *, skip_disabled: bool = True) -> dict:
    episode = "smoke-" + str(uuid4())
    image = fixture_image()
    reference = None
    results = []
    for service in services:
        if service == Service.GENIE and not gateway.config.genie_enabled and skip_disabled:
            results.append({"service": service.value, "outcome": "skipped", "reason": "disabled", "optional": True})
            continue
        context = CallContext(episode_id=episode, prompt_id="capability-v1-" + service.value, purpose="smoke")
        try:
            if service == Service.HYDROGEN:
                result = await gateway.generate(service, stage="observation", context=context,
                    input_value=[{"type": "text", "text": "Describe the illustrated street and count its visible colored vehicle rectangles. This is a synthetic capability test."}, image],
                    response_model=SmokeObservation)
            elif service == Service.CARBON:
                result = await gateway.generate(service, stage="compilation", context=context,
                    input_value="Synthetic capability test: a street has two vehicle lanes, sidewalks and a marked crossing. Compile a request to add a protected bike lane, preserving the sidewalks and crossing. State the change and preserved features; do not claim metric feasibility.",
                    response_model=SmokeEdit)
            elif service == Service.NANO_BANANA:
                result = await gateway.generate(service, stage="reference", context=context,
                    input_value=[{"type": "text", "text": "Turn this synthetic street illustration into a clean cel-shaded street view with a protected bike lane. Use a 16:9 image."}, image])
                reference = {k: result.output[k] for k in ("mime_type", "data")}
            elif service == Service.DECISIONS:
                result = await gateway.judge(stage="image_gate", context=context,
                    input_value=[{"type": "text", "text": "Inspect this synthetic street illustration."}, image],
                    questions={"colored_vehicles": {"type": "noul", "instructions": "Are colored vehicle rectangles visible on the gray street?"}})
            else:
                result = await gateway.probe_world(context=context,
                    prompt="A quiet cel-shaded street with a protected bike lane. First-person view from the sidewalk.",
                    reference_image=reference)
            results.append({"service": service.value, "outcome": "success", "request_id": result.request_id,
                "provider_request_id": result.provider_request_id,
                "provider_request_id_status": "returned" if result.provider_request_id else "not_returned",
                "model_id": result.model_id, "input_hash": result.input_hash, "result_hash": result.result_hash,
                "usage": result.usage.model_dump() if result.usage else None, "accounting": result.accounting})
        except GatewayError as error:
            results.append({"service": service.value, "outcome": "blocked_or_failed", "error": error.public()})
    report = {"schema_version": 1, "episode_id": episode, "kind": "development_capability_smoke",
              "results": results, "status": gateway.status(),
              "all_selected_verified": any(r["outcome"] != "skipped" for r in results) and all(r["outcome"] == "success" and r["usage"] is not None
                  and r["request_id"] and (r["service"] != Service.GENIE.value or r["provider_request_id"])
                  for r in results if r["outcome"] != "skipped"),
              "request_id_note": "request_id is the local durable attempt ID. Stateless inference and Decisions may omit provider IDs; Genie requires an interaction ID for termination.",
              "quota_measurement": "One bounded probe per selected service, plus logged retries. Project throughput ceilings remain unmeasured.",
              "submission_evidence": False}
    fingerprint = gateway.ledger.artifact(canonical(report))
    return {**report, "report_hash": fingerprint}


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inspect gateway status; --live performs bounded, billable capability probes.")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--live", action="store_true")
    actions.add_argument("--cancel-world", metavar="REQUEST_ID", help="Cancel a recorded interrupted Genie interaction; does not start a world.")
    parser.add_argument("--services", nargs="+", choices=[s.value for s in Service], default=None)
    args = parser.parse_args(argv)
    try:
        config = GatewayConfig.from_environment()
        async with httpx.AsyncClient(follow_redirects=False) as client:
            gateway = ModelGateway(config, client)
            if args.cancel_world:
                report = await gateway.cancel_world(args.cancel_world)
            else:
                report = await run_smoke(gateway, list(dict.fromkeys(Service(s) for s in (args.services or list(Service)))),
                                         skip_disabled=args.services is None) if args.live else gateway.status()
            print(json.dumps(report, indent=2))
            return 0 if not args.live or report["all_selected_verified"] else 1
    except GatewayError as error:
        print(json.dumps({"error": error.public()}))
        return 1
    except (OSError, ValueError, ValidationError):
        print(json.dumps({"error": "Invalid gateway configuration or inaccessible local evidence directory."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
