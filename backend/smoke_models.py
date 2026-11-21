"""Explicit, billable capability probes; never run automatically at app startup."""
import argparse
import asyncio
import json

from .config import load_environment
from .gateway import GatewayError, ModelGateway, MODELS

PAYLOADS = {
    "hydrogen": {"input": "Report the two qualifying model roles in one sentence: Hydrogen observes evidence; Carbon compiles edits.", "generation_config": {"max_output_tokens": 2048}},
    "carbon": {"input": "Compile this request into one JSON object with vehiclePermissions and unknownGeometry: add a protected bike lane. Do not assert metric geometry or safety.", "generation_config": {"max_output_tokens": 2048}},
    "nano_banana": {"input": "Generate a simple illustration of an empty city street with a green protected bicycle lane.", "response_format": {"type": "image", "aspect_ratio": "16:9", "image_size": "1K"}, "generation_config": {"max_output_tokens": 8192}},
    "decisions": {"input": "A protected bike lane allows bicycles and excludes cars.", "questions": {"bikes_allowed": {"type": "noul", "instructions": "Are bicycles allowed?"}}},
    "genie": {"world_prompt": {"text": "First-person view of an empty stylized city street in daylight. Remain stationary."}},
}


async def run_smokes(services: list[str], *, demo: bool = False, skip_disabled: bool = False) -> int:
    load_environment()
    gateway = ModelGateway.from_env()
    failures = 0
    try:
        for service in services:
            if skip_disabled and service == "genie" and not gateway.config.genie_enabled:
                print(json.dumps({"service": service, "modelId": MODELS[service], "outcome": "skipped", "reason": "disabled"}))
                continue
            try:
                # Conservative reservation, not a provider quote or measured charge.
                result = await gateway.run(service, PAYLOADS[service], prompt_id=f"smoke-{service}-v1", config_id="smoke-v1",
                                           reserve_tokens=16_384, reserve_usd=4 if service == "genie" else 1, demo=demo)
                print(json.dumps({"service": service, "trace": result.trace, "latencyMs": result.metadata["latencyMs"],
                                  "outcome": "success", "actualCostUsd": None}))
            except GatewayError as error:
                failures += 1
                print(json.dumps({"service": service, "modelId": MODELS[service], "outcome": "blocked", "error": error.public()}))
        print(json.dumps({"status": gateway.status(), "note": "Capability probes do not measure project maximum quotas or satisfy episode evaluation."}))
    finally:
        await gateway.close()
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", choices=["all", *MODELS], default="all", help="all skips Genie unless GENIE_ENABLED=true")
    parser.add_argument("--live", action="store_true", help="Make real, billable provider requests")
    parser.add_argument("--use-demo-reserve", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to explicitly authorize billable capability probes.")
    raise SystemExit(asyncio.run(run_smokes(list(MODELS) if args.service == "all" else [args.service], demo=args.use_demo_reserve, skip_disabled=args.service == "all")))


if __name__ == "__main__":
    main()
