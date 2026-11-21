"""Decode only the pinned prefix. Frame indices and times come from ffprobe."""
from __future__ import annotations

from io import BytesIO
import json
import math
from pathlib import Path
import shutil
import subprocess

from PIL import Image

from .artifacts import ArtifactError, ArtifactStore, sha256
from .capture import CaptureStore
from .contracts import BoxRegion

FULL_FRAME = BoxRegion(kind="box", x=0, y=0, w=1, h=1)


def _command(args):
    try:
        return subprocess.run(args, capture_output=True, check=True, timeout=60).stdout
    except (subprocess.SubprocessError, OSError):
        raise ArtifactError("The saved prefix could not be decoded.", status=422) from None


def index_prefix(path: Path, manifest: dict) -> list[dict]:
    if manifest["sourceKind"] == "snapshot":
        return [{"frameIndex": 0, "mediaTimeS": 0.0}]
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        raise ArtifactError("ffprobe and ffmpeg are required for continuous evidence.", status=503)
    raw = _command(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
                    "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path)])
    try:
        times = [float(f["best_effort_timestamp_time"]) for f in json.loads(raw)["frames"]]
        if not times or not all(math.isfinite(t) for t in times) or any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError()
        rows = [{"frameIndex": i, "mediaTimeS": round(t - times[0], 6)} for i, t in enumerate(times)]
        duration = manifest["files"]["prefix"]["durationS"]
        if manifest["frameCount"] is None or len(rows) != manifest["frameCount"]:
            raise ValueError()
        if rows[-1]["mediaTimeS"] > duration + .1:
            raise ValueError()
        return [row for row in rows if row["mediaTimeS"] < duration]
    except (KeyError, TypeError, ValueError):
        raise ArtifactError("The prefix frame/timestamp audit failed; no truncated or guessed timeline is accepted.", status=422) from None


def choose_frames(index: list[dict], start: float, end: float, maximum: int) -> list[dict]:
    available = [r for r in index if start <= r["mediaTimeS"] <= end]
    if not available:
        raise ArtifactError("No original frame exists inside the requested interval.", status=422)
    if len(available) <= maximum:
        return available
    picks = sorted({round(i * (len(available) - 1) / (maximum - 1)) for i in range(maximum)})
    return [available[i] for i in picks]


def original_frame(store: ArtifactStore, path: Path, manifest: dict, indexed: dict) -> dict:
    capture_id, frame_index = manifest["id"], indexed["frameIndex"]
    record_id = f"frame_{capture_id.removeprefix('cap_')}-n{frame_index}"
    media_hash = manifest["files"]["prefix"]["sha256"]
    if store.exists("frames", record_id):
        record = store.get("frames", record_id)
        if (record["captureId"] != capture_id or record["captureMediaSha256"] != media_hash
                or record["frameIndex"] != frame_index or record["mediaTimeS"] != indexed["mediaTimeS"]):
            raise ArtifactError("Stored frame provenance does not match the prefix.", status=409)
        store.get_blob(record["frameSha256"])
        return record
    if manifest["sourceKind"] == "snapshot":
        data = path.read_bytes()
    else:
        data = _command(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{frame_index})",
                         "-frames:v", "1", "-fps_mode", "passthrough", "-f", "image2", "-c:v", "png", "pipe:1"])
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            mime = {"PNG": "image/png", "JPEG": "image/jpeg"}[image.format]
            image.verify()
    except (OSError, ValueError, KeyError, Image.DecompressionBombError):
        raise ArtifactError("The original frame could not be decoded.", status=422) from None
    record = {"id": record_id, "captureId": capture_id, "captureMediaSha256": media_hash,
              "sourceId": manifest["cameraId"], **indexed, "frameSha256": store.put_blob(data),
              "mimeType": mime, "width": width, "height": height, "provenance": "observed"}
    return store.put("frames", record_id, record)


def inspection_image(store: ArtifactStore, frame: dict, roi: BoxRegion) -> dict:
    data = store.get_blob(frame["frameSha256"])
    if roi == FULL_FRAME:
        return {"sha256": frame["frameSha256"], "mimeType": frame["mimeType"], "roi": roi.model_dump()}
    with Image.open(BytesIO(data)) as image:
        w, h = image.size
        left, top = math.floor(roi.x * w), math.floor(roi.y * h)
        right, bottom = math.ceil((roi.x + roi.w) * w), math.ceil((roi.y + roi.h) * h)
        cropped = image.crop((left, top, min(w, right), min(h, bottom)))
        output = BytesIO()
        cropped.save(output, format="PNG")
    # Use the actual rounded pixel bounds for the inverse coordinate transform.
    actual = BoxRegion(kind="box", x=left/w, y=top/h, w=(min(w,right)-left)/w, h=(min(h,bottom)-top)/h)
    return {"sha256": store.put_blob(output.getvalue()), "mimeType": "image/png", "roi": actual.model_dump()}


def source_region(region: BoxRegion, crop: dict) -> BoxRegion:
    return BoxRegion(kind="box", x=crop["x"] + region.x * crop["w"], y=crop["y"] + region.y * crop["h"],
                     w=region.w * crop["w"], h=region.h * crop["h"])


def verified_prefix(captures: CaptureStore, capture_id: str, camera_id: str):
    path, fingerprint = captures.model_input(capture_id, camera_id)
    manifest = captures.get(capture_id)
    expected = (captures.root / capture_id / "prefix").resolve()
    if not path.resolve().is_relative_to(expected) or sha256(path.read_bytes()) != fingerprint:
        raise ArtifactError("Only verified original prefix media may be inspected.", status=403)
    return path, manifest
