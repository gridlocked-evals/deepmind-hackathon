from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import tempfile
import time
from uuid import UUID
from io import BytesIO

from PIL import Image

from ..gateway.adapters import decode_image
from ..gateway.contracts import ErrorCode, GatewayError, canonical, digest
from .contracts import AcceptedReference, WorldRequest


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def valid_reference(image: dict) -> bytes:
    if set(image) != {"mime_type", "data"} or len(image.get("data", "")) > 8 * 1024 * 1024:
        raise GatewayError(ErrorCode.INVALID_INPUT)
    try:
        data, (width, height) = decode_image(image)
        if width * 9 != height * 16:
            raise ValueError()
        with Image.open(BytesIO(data)) as decoded:
            if Image.MIME[decoded.format] != image["mime_type"]:
                raise ValueError()
        return data
    except (GatewayError, ValueError):
        raise GatewayError(ErrorCode.INVALID_INPUT) from None


class ReferenceStore:
    """Only trusted Python gate code can publish; there is no HTTP write route."""
    def __init__(self, root: Path):
        self.root = root / "accepted_references"

    def publish(self, record: AcceptedReference, image: bytes, gate_artifact: bytes) -> str:
        # The gate owns the decision. This boundary pins its exact record and bytes.
        if digest(image) != record.reference_sha256 or digest(gate_artifact) != record.gate_artifact_sha256:
            raise GatewayError(ErrorCode.INVALID_INPUT)
        valid_reference({"mime_type": record.mime_type, "data": base64.b64encode(image).decode()})
        body = canonical(record.model_dump())
        fingerprint = digest(body)
        atomic_write(self.root / f"{record.reference_sha256}.image", image)
        atomic_write(self.root / f"{record.gate_artifact_sha256}.gate", gate_artifact)
        atomic_write(self.root / f"{fingerprint}.json", body)
        return fingerprint

    def load(self, request: WorldRequest) -> tuple[AcceptedReference, dict]:
        try:
            body = (self.root / f"{request.accepted_reference}.json").read_bytes()
            if digest(body) != request.accepted_reference:
                raise ValueError()
            record = AcceptedReference.model_validate_json(body)
            context = request.context
            if context.purpose not in ("submission", "demo") or any(
                getattr(context, field) != getattr(record, field)
                for field in ("episode_id", "source_id", "capture_id")
            ):
                raise ValueError()
            data = (self.root / f"{record.reference_sha256}.image").read_bytes()
            gate = (self.root / f"{record.gate_artifact_sha256}.gate").read_bytes()
            if digest(data) != record.reference_sha256 or digest(gate) != record.gate_artifact_sha256:
                raise ValueError()
            image = {"mime_type": record.mime_type, "data": base64.b64encode(data).decode()}
            valid_reference(image)
            return record, image
        except (OSError, ValueError):
            raise GatewayError(ErrorCode.INVALID_INPUT) from None


class Recording:
    def __init__(self, root: Path, session_id: str):
        self.path = root / "worlds" / session_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.started = time.monotonic()
        self.frame_index = -1
        self.bytes = 0
        self.sequence = 0

    def event(self, kind: str, **fields):
        self.sequence += 1
        row = {"sequence": self.sequence, "kind": kind, "frame_index": self.frame_index,
               "received_at": time.time(), "elapsed_ms": round((time.monotonic() - self.started) * 1000, 3), **fields}
        with (self.path / "events.jsonl").open("ab") as handle:
            handle.write(canonical(row) + b"\n")
            handle.flush()
        return row

    def frame(self, data: bytes, size: tuple[int, int]):
        self.frame_index += 1
        name = f"frames/{self.frame_index:06d}.jpg"
        atomic_write(self.path / name, data)
        self.bytes += len(data)
        return self.event("frame", file=name, sha256=digest(data), width=size[0], height=size[1])

    def summary(self, value: dict):
        atomic_write(self.path / "session.json", canonical(value))


def recorded_results(root: Path, session_id: str) -> dict:
    """Read retained runtime evidence without claiming an unrun evaluation."""
    if str(UUID(session_id)) != session_id:
        raise ValueError("Invalid session id")
    path = root / "worlds" / session_id
    summary = json.loads((path / "session.json").read_bytes())
    if summary.get("session_id") != session_id:
        raise ValueError("Recording identity mismatch")
    rows = []
    events = path / "events.jsonl"
    if events.exists():
        # An active writer can leave a partial final line; only complete records
        # are reviewable. All malformed completed rows fail closed.
        data = events.read_bytes()
        rows = [json.loads(line) for line in data.splitlines(keepends=True) if line.endswith(b"\n")]
    frames = [{key: row[key] for key in ("frame_index", "received_at", "elapsed_ms", "sha256", "width", "height")}
              for row in rows if row.get("kind") == "frame"]
    for expected, frame in enumerate(frames):
        if frame["frame_index"] != expected:
            raise ValueError("Recording frame order is invalid")
        data = (path / "frames" / f"{expected:06d}.jpg").read_bytes()
        if digest(data) != frame["sha256"]:
            raise ValueError("Recording frame no longer matches its hash")
    controls = [{key: row[key] for key in ("kind", "frame_index", "elapsed_ms", "key", "owner", "hold_ms", "reason") if key in row}
                for row in rows if row.get("kind") in {
                    "key_down_requested", "key_down_sent", "key_up", "takeover", "stop_requested", "termination"}]
    return {"session": summary, "mediaProvenance": "generated_recording", "frames": frames,
            "controls": controls, "temporalJudge": {"status": "not_run", "coveredFrames": 0,
                "uncoveredFrames": len(frames),
                "reason": "The live temporal judge is not connected to this runtime. Recorded frames do not establish safety."}}
