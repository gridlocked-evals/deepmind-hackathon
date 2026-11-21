"""Shared fakes for the edit/reference/gate tests: a real ModelGateway over a scripted provider."""
from __future__ import annotations

import asyncio
import base64
from datetime import date
from io import BytesIO
import json
from pathlib import Path

import httpx
from PIL import Image

from backend.artifacts import ArtifactStore
from backend.contracts import ObservationBatch
from backend.edits import compile_edit
from backend.gateway import GatewayConfig, ModelGateway

KEY = "test-secret-never-record-this"
USAGE = {"total_input_tokens": 100, "total_output_tokens": 50, "total_thought_tokens": 0, "total_tokens": 150}
FIXTURE = Path(__file__).resolve().parent.parent / "contracts" / "fixtures" / "vertical_slice.v1.json"


def png(size=(1376, 768), color=(40, 120, 60)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg(size=(704, 480), color=(90, 90, 90)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


def batch() -> ObservationBatch:
    return ObservationBatch.model_validate(json.loads(FIXTURE.read_text())["observationBatch"])


def stored_frame(store: ArtifactStore, capture_id: str = "cap_fixture-0001") -> dict:
    data = jpeg()
    record = {"id": "frame_fixture-0001-t0", "captureId": capture_id, "captureMediaSha256": "sha256:" + "a" * 64,
              "mediaTimeS": 0.0, "frameSha256": store.put_blob(data), "mimeType": "image/jpeg",
              "width": 704, "height": 480, "provenance": "observed"}
    return store.put("frames", record["id"], record)


CURB = [[0.56, 0.62], [0.94, 0.62], [0.97, 0.97], [0.61, 0.97]]


def carbon_ok(**changes) -> dict:
    out = {
        "status": "compiled", "reason": "Curb lane becomes a planter-protected bike lane.",
        "operations": [
            {"op": "modify", "target": "parking_lane", "becomes": "bike_lane", "points": CURB,
             "anchorIds": ["anc_fixture-curb"], "evidenceObservationIds": ["ob_fixture-van"],
             "description": "Convert the curb parking lane into a green protected bike lane."},
            {"op": "add", "target": "planter", "points": [[0.52, 0.6], [0.57, 0.6], [0.6, 0.97], [0.55, 0.97]],
             "anchorIds": ["anc_fixture-curb"], "evidenceObservationIds": [],
             "description": "Add a planter buffer between traffic and the bike lane."},
        ],
        "laneUse": {"allowed": ["cyclist"], "forbidden": ["general_traffic", "delivery"]},
        "preservedAnchorIds": ["anc_fixture-xwalk"],
        "unknownGeometry": [{"field": "curbLineBehindVan", "reason": "Occluded by the double-parked van."}],
        "imageInstructions": "Repaint the right curb lane green with bike symbols and add a planter row on its left.",
        "worldPrompt": "First-person view walking north along a street with a new green protected bike lane.",
    }
    out.update(changes)
    return out


class FakeProvider:
    """Scripted responses per service. Each script entry is a dict, bytes, or an exception/status int."""

    def __init__(self):
        self.carbon: list = []
        self.images: list = []
        self.decisions: list = []
        self.requests: list[tuple[str, dict]] = []

    @staticmethod
    def _interaction(content):
        return {"id": "provider-req", "status": "completed", "usage": USAGE,
                "steps": [{"type": "model_output", "content": content}]}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        model = body.get("model")
        self.requests.append((model, body))
        queue = {"gemini-4-carbon": self.carbon, "gemini-nano-banana-2.1": self.images,
                 "gemini-decision-1": self.decisions}[model]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, int):
            return httpx.Response(item, json={"error": "scripted"})
        if model == "gemini-4-carbon":
            return httpx.Response(200, json=self._interaction([{"type": "text", "text": json.dumps(item)}]))
        if model == "gemini-nano-banana-2.1":
            return httpx.Response(200, json=self._interaction(
                [{"type": "image", "mime_type": "image/png", "data": base64.b64encode(item).decode()}]))
        questions = body["questions"]
        answers = {name: {"type": "noul", "noul": item.get(name, item["*"])[0],
                          "confidence": item.get(name, item["*"])[1]} for name in questions}
        return httpx.Response(200, json={"model": "gemini-decision-1", "answers": answers, "usage": USAGE})

    def count(self, model: str) -> int:
        return sum(1 for m, _ in self.requests if m == model)


def gateway(tmp_path, client) -> ModelGateway:
    config = GatewayConfig(data_dir=tmp_path / "ledger", rates_valid_through=date(2030, 1, 1),
                           retry_delay_seconds=0)
    return ModelGateway(config, client, api_key=KEY)


def client(provider: FakeProvider) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(provider.handler))


YES = {"*": (0.95, 0.95)}


def answers(**overrides) -> dict:
    return {"*": (0.95, 0.95), **overrides}


def setup(tmp_path, provider, edit_output=None):
    """Compile one edit through the fake provider and return (store, bundle)."""
    store = ArtifactStore(tmp_path / "pipeline")
    provider.carbon = [edit_output or carbon_ok()]

    async def run():
        async with client(provider) as http:
            frame = stored_frame(store)
            return await compile_edit(gateway(tmp_path, http), store, batch=batch(),
                                      preset_name="protected_bike_lane", mayor_request="Protected bike lane, please.",
                                      frame=frame, capture_media_sha256=frame["captureMediaSha256"])
    return store, asyncio.run(run())


def run(tmp_path, provider, fn):
    async def go():
        async with client(provider) as http:
            return await fn(gateway(tmp_path, http))
    return asyncio.run(go())
