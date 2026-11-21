"""Original source frames for Carbon and Nano Banana.

Select the frame displayed at or immediately before a requested prefix time.
Frame indices and stored timestamps come from the decoded prefix, never a seek
request rounded to milliseconds. Holdout files are never passed to the decoder.
"""
from __future__ import annotations

import math
import secrets

from .artifacts import ArtifactError, ArtifactStore
from .capture import CaptureStore
from .observation_media import index_prefix, original_frame


def source_frame(store: ArtifactStore, captures: CaptureStore, capture_id: str, at_s: float = 0.0) -> dict:
    """Return a verified original frame with its actual decoded index and PTS.

    Requests in a frame's display interval share the same immutable frame record.
    A durable temporary pin prevents storage cleanup while the prefix is verified,
    indexed and decoded. A crash leaves the pin retained for explicit recovery.
    """
    if isinstance(at_s, bool) or not isinstance(at_s, (int, float)) or not math.isfinite(at_s) or at_s < 0:
        raise ArtifactError("Frame time must be a finite nonnegative number.", status=422)
    pin_id = f"frame-read-{secrets.token_hex(16)}"
    captures.pin(capture_id, pin_id)
    try:
        manifest = captures.get(capture_id)
        if manifest["sourceKind"] == "snapshot":
            if at_s != 0:
                raise ArtifactError("Snapshot captures have a single frame at time 0.", status=422)
        elif not 0 <= at_s < manifest["files"]["prefix"]["durationS"]:
            raise ArtifactError("Frame time is outside the half-open model-visible prefix.", status=422,
                                mediaTimeS=at_s, durationS=manifest["files"]["prefix"]["durationS"])
        path, media_hash = captures.model_input(capture_id)
        index = index_prefix(path, manifest)
        candidates = [row for row in index if row["mediaTimeS"] <= at_s]
        if not candidates:
            raise ArtifactError("No original frame exists at this prefix time.", status=422)
        indexed = candidates[-1]
        record = original_frame(store, path, manifest, indexed)
        # original_frame validates cached capture/hash/index/time and blob bytes.
        # Keep camera identity explicit at this second downstream boundary too.
        if (record.get("sourceId") != manifest["cameraId"] or record.get("captureId") != capture_id
                or record.get("captureMediaSha256") != media_hash
                or record.get("frameIndex") != indexed["frameIndex"]
                or record.get("mediaTimeS") != indexed["mediaTimeS"]):
            raise ArtifactError("Stored source-frame provenance does not match the capture.", status=409)
        return record
    finally:
        captures.unpin(capture_id, pin_id)
