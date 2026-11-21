"""Original source frames for Carbon and Nano Banana.

A snapshot episode is its own frame. For HLS episodes one frame is decoded from
the model-visible prefix (never the holdout) with ffmpeg and stored by hash, so
every retry and every later stage sees identical original pixels.
"""
from __future__ import annotations

from io import BytesIO
import shutil
import subprocess

from PIL import Image

from .artifacts import ArtifactError, ArtifactStore, sha256
from .capture import CaptureStore

FRAME_MIME = {"PNG": "image/png", "JPEG": "image/jpeg"}


def _decode(data: bytes) -> tuple[str, int, int]:
    try:
        with Image.open(BytesIO(data)) as image:
            fmt, (w, h) = image.format, image.size
            image.verify()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ArtifactError("The source frame could not be decoded.", status=422) from None
    if fmt not in FRAME_MIME:
        raise ArtifactError("The source frame is not PNG or JPEG.", status=422, format=fmt)
    return FRAME_MIME[fmt], w, h


def _extract(path, at_s: float) -> bytes:
    if not shutil.which("ffmpeg"):
        raise ArtifactError("ffmpeg is required to decode a frame from HLS evidence.", status=503)
    try:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-ss", f"{at_s:.3f}", "-frames:v", "1",
             "-f", "image2", "-c:v", "png", "pipe:1"], capture_output=True, timeout=60, check=True)
    except (subprocess.SubprocessError, OSError):
        raise ArtifactError("ffmpeg could not decode a frame from the capture.", status=422) from None
    if not out.stdout:
        raise ArtifactError("The capture has no decodable frame at that time.", status=422, mediaTimeS=at_s)
    return out.stdout


def source_frame(store: ArtifactStore, captures: CaptureStore, capture_id: str, at_s: float = 0.0) -> dict:
    """Return the stored frame record, extracting it on first use."""
    record_id = f"frame_{capture_id.removeprefix('cap_')}-t{int(round(at_s * 1000))}"
    if store.exists("frames", record_id):
        record = store.get("frames", record_id)
        store.get_blob(record["frameSha256"])  # re-verify bytes
        return record
    path, media_hash = captures.model_input(capture_id)  # verifies the capture hash; prefix only
    manifest = captures.get(capture_id)
    if manifest["sourceKind"] == "snapshot":
        if at_s != 0:
            raise ArtifactError("Snapshot captures have a single frame at time 0.", status=422)
        data = path.read_bytes()
    else:
        duration = manifest["files"]["prefix"]["durationS"]
        if not 0 <= at_s <= duration:
            raise ArtifactError("Frame time is outside the model-visible capture.", status=422,
                                mediaTimeS=at_s, durationS=duration)
        data = _extract(path, at_s)
    mime, width, height = _decode(data)
    record = {"id": record_id, "captureId": capture_id, "captureMediaSha256": media_hash, "mediaTimeS": at_s,
              "frameSha256": store.put_blob(data), "mimeType": mime, "width": width, "height": height,
              "provenance": "observed"}
    assert record["frameSha256"] == sha256(data)
    return store.put("frames", record_id, record)
