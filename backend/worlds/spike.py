"""Opt-in, bounded live development recording. Never publishes gate acceptance."""
import argparse
import asyncio
from decimal import Decimal
import json

import httpx

from ..gateway import GatewayConfig, GatewayError, ModelGateway
from ..gateway.smoke import fixture_image
from .runtime import WorldManager

PROMPT = (
    "Development driving-visualization test. First-person dashboard view of a stationary "
    "taxi on a quiet straight city street. Keep continuous perspective. W moves forward "
    "along the road; releasing W removes that input. Sidewalks, crossing stripes, and "
    "buildings remain visible. Start without moving. This is a fictional visual world."
)


async def run_spike(config):
    async with httpx.AsyncClient(follow_redirects=False) as client:
        manager = WorldManager(ModelGateway(config, client))
        image = fixture_image()
        session = manager.development(prompt=PROMPT,
            image={"mime_type": image["mime_type"], "data": image["data"]}, seconds=20, max_usd=Decimal("3"))
        for role in ("human", "adversary"):
            await session.attach(role, session.tokens[role])

        async def heartbeat():
            while not session.stopping.is_set() and not session.finished.is_set():
                for role in ("human", "adversary"):
                    await session.command({"kind": "heartbeat"}, role)
                await asyncio.sleep(.5)

        async def actions():
            await session.ready.wait()
            while session.recording.frame_index < 0 and not session.finished.is_set():
                await asyncio.sleep(.02)
            await asyncio.sleep(1)
            await session.command({"kind": "key", "owner": "human", "key": "key_w", "hold_ms": 500}, "human")
            await asyncio.sleep(2)
            await session.command({"kind": "steer", "owner": "adversary", "text":
                "A large bright blue bus enters the road directly ahead from the right and remains visible."}, "adversary")
            await asyncio.sleep(5)
            await session.stop("spike_finished")

        tasks = [asyncio.create_task(heartbeat()), asyncio.create_task(actions())]
        try:
            await session.finished.wait()
            await session.task
        finally:
            for task in tasks:
                task.cancel()
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            await manager.close()
        errors = [type(value).__name__ for value in outcomes if isinstance(value, Exception)]
        return {**session.status(), "recording_directory": str(session.recording.path),
                "action_script_errors": errors,
                "measurement_status": "requires frame review; command delivery alone does not establish visible response",
                "project_quota": "single-session access only; maximum concurrency/daily limits unmeasured"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Authorize one development session, at most 20s plus cleanup / $3 local reservation")
    args = parser.parse_args()
    if not args.live:
        print("No paid calls made. Use --live for one bounded development recording.")
        return 0
    try:
        report = asyncio.run(run_spike(GatewayConfig.from_environment()))
    except GatewayError as exc:
        print(json.dumps(exc.public()))
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report["state"] == "completed" and report["frames_received"] and not report["action_script_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
