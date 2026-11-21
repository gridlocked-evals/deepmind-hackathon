"""Prepare immutable permitted camera pixels with measured presentation timestamps.

No network access or camera recapture occurs here. Only CaptureService.read_prefix
can supply bytes, and the sealed suffix never enters a decoder or model payload.
"""
from __future__ import annotations

import asyncio
from bisect import bisect_left
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from .capture import CaptureService
from .contracts import CaptureContext, EvidenceRef, ImageRegion, TimeInterval

DEFAULT_MAX_FRAMES = 121
DEFAULT_MAX_PAYLOAD_BYTES = 10 * 1024 * 1024
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_PROBE_JSON_BYTES = 32 * 1024 * 1024
MAX_DECODED_FRAMES = 100_000
FULL_IMAGE = ImageRegion(x=0.0, y=0.0, width=1.0, height=1.0)


def _error(status: int, message: str) -> HTTPException:
    return HTTPException(status, message)


@dataclass(frozen=True)
class EvidenceFrame:
    data: bytes
    mime_type: str
    time_seconds: float
    frame_index: int | None
    region: ImageRegion
    width: int
    height: int
    source_media_index: int
    sha256: str
    capture_id: str
    original_media_sha256: str
    source_pts_seconds: float | None

    def to_evidence_ref(self, region: ImageRegion | dict | None = None) -> EvidenceRef:
        """Map a normalized ROI in this supplied image back onto original pixels."""
        relative = FULL_IMAGE if region is None else ImageRegion.model_validate(region)
        mapped = ImageRegion(
            x=self.region.x + relative.x * self.region.width,
            y=self.region.y + relative.y * self.region.height,
            width=relative.width * self.region.width,
            height=relative.height * self.region.height,
        )
        return EvidenceRef(captureId=self.capture_id, originalMediaSha256=self.original_media_sha256,
                           timeSeconds=self.time_seconds, frameIndex=self.frame_index, region=mapped)

    def metadata(self) -> dict[str, Any]:
        return {"timeSeconds": self.time_seconds, "frameIndex": self.frame_index,
                "sourceMediaIndex": self.source_media_index, "sourcePtsSeconds": self.source_pts_seconds,
                "imageSha256": self.sha256, "mimeType": self.mime_type, "byteLength": len(self.data),
                "width": self.width, "height": self.height, "sourceRegion": self.region.model_dump()}


@dataclass(frozen=True)
class PreparedEvidence:
    capture: CaptureContext
    frames: tuple[EvidenceFrame, ...]
    sampling: dict[str, Any]

    def metadata(self) -> dict[str, Any]:
        """Safe model-input metadata: no suffix paths, suffix counts, or raw media."""
        return {"capture": self.capture.model_dump(), "sampling": self.sampling,
                "frames": [{"frameId": index, **frame.metadata()} for index, frame in enumerate(self.frames)]}


@dataclass(frozen=True)
class _DecodedFrame:
    segment_index: int
    local_index: int
    frame_index: int
    time_seconds: float
    pts_seconds: float | None


@dataclass(frozen=True)
class _Segment:
    media_index: int
    path: Path
    data: bytes | None
    mime: str
    width: int
    height: int
    first_pts: float | None
    decoded_count: int


async def _run(command: list[str], *, output_limit: int = MAX_PROBE_JSON_BYTES, timeout: float = 30) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE)
    except (FileNotFoundError, OSError):
        raise _error(503, "ffmpeg and ffprobe are required to prepare camera evidence.") from None

    async def read_bounded(stream: asyncio.StreamReader, maximum: int) -> bytes:
        output = bytearray()
        while chunk := await stream.read(64 * 1024):
            if len(output) + len(chunk) > maximum:
                raise _error(413, "Media decoder output exceeds the evidence preparation budget.")
            output.extend(chunk)
        return bytes(output)

    readers = [asyncio.create_task(read_bounded(process.stdout, output_limit)),
               asyncio.create_task(read_bounded(process.stderr, 256 * 1024))]
    try:
        output, errors = await asyncio.wait_for(asyncio.gather(*readers), timeout)
        await process.wait()
        if process.returncode or errors.strip():
            raise _error(502, "Camera evidence could not be fully decoded; no partial input was accepted.")
        return output
    except (BaseException,):
        if process.returncode is None:
            process.kill()
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await process.wait()
        raise


async def _probe(path: Path, *, snapshot: bool) -> dict:
    output = await _run([
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", "v:0",
        "-show_frames", "-show_entries", "frame=best_effort_timestamp_time,width,height:stream=width,height",
        "-of", "json", str(path),
    ])
    try:
        value = json.loads(output)
        frames = value["frames"]
        stream = value["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
        if not isinstance(frames, list) or not frames or len(frames) > MAX_DECODED_FRAMES or min(width, height) <= 0:
            raise ValueError()
        if snapshot and len(frames) != 1:
            raise ValueError()
        pts = []
        for frame in frames:
            if int(frame["width"]) != width or int(frame["height"]) != height:
                raise ValueError()
            if snapshot:
                pts.append(None)
            else:
                timestamp = float(frame["best_effort_timestamp_time"])
                if not math.isfinite(timestamp) or (pts and timestamp <= pts[-1]):
                    raise ValueError()
                pts.append(timestamp)
    except (KeyError, TypeError, ValueError, IndexError):
        raise _error(502, "Frame count, dimensions, or presentation timestamps failed the evidence audit.") from None
    return {"width": width, "height": height, "pts": pts}


def _crop_pixels(region: ImageRegion, width: int, height: int) -> tuple[int, int, int, int, ImageRegion]:
    x, y = math.floor(region.x * width), math.floor(region.y * height)
    end_x = min(width, math.ceil((region.x + region.width) * width))
    end_y = min(height, math.ceil((region.y + region.height) * height))
    crop_width, crop_height = end_x - x, end_y - y
    if min(crop_width, crop_height) <= 0:
        raise _error(422, "The requested evidence crop contains no source pixels.")
    actual = ImageRegion(x=x / width, y=y / height, width=crop_width / width, height=crop_height / height)
    return x, y, crop_width, crop_height, actual


def _sample(frames: list[_DecodedFrame], max_frames: int) -> tuple[list[_DecodedFrame], float]:
    if len(frames) <= 1:
        return frames, 1.0
    span = frames[-1].time_seconds - frames[0].time_seconds
    spacing = max(1.0, span / (max_frames - 1))
    times = [frame.time_seconds for frame in frames]
    selected = [frames[0]]
    while len(selected) < max_frames - 1:
        next_index = bisect_left(times, selected[-1].time_seconds + spacing - 1e-9)
        if next_index >= len(frames) - 1:
            break
        selected.append(frames[next_index])
    if frames[-1] != selected[-1]:
        selected.append(frames[-1])
    return selected, spacing


def _jpeg_images(data: bytes) -> list[bytes]:
    """Split bounded image2pipe output using JPEG segment lengths, not byte guesses."""
    images = []
    position = 0
    try:
        while position < len(data):
            start = position
            if data[position:position + 2] != b"\xff\xd8":
                raise ValueError()
            position += 2
            while True:
                if data[position] != 0xFF:
                    raise ValueError()
                while data[position] == 0xFF:
                    position += 1
                marker = data[position]
                position += 1
                if marker == 0xD9:
                    images.append(data[start:position])
                    break
                if marker in {0, 0xD8}:
                    raise ValueError()
                length = int.from_bytes(data[position:position + 2], "big")
                if length < 2 or position + length > len(data):
                    raise ValueError()
                position += length
                if marker == 0xDA:
                    # Entropy data escapes FF bytes and permits restart markers.
                    while True:
                        position = data.index(b"\xff", position)
                        next_byte = data[position + 1]
                        if next_byte == 0 or 0xD0 <= next_byte <= 0xD7:
                            position += 2
                            continue
                        break
    except (ValueError, IndexError):
        raise _error(502, "Generated evidence image framing audit failed.") from None
    return images


async def _prepare(captures: CaptureService, capture_id: str, *, window: TimeInterval | None,
                   crop: ImageRegion | None, max_frames: int, max_payload_bytes: int) -> PreparedEvidence:
    if isinstance(max_frames, bool) or not isinstance(max_frames, int) or not 1 <= max_frames <= DEFAULT_MAX_FRAMES:
        raise _error(422, "Evidence frame budget must be an integer from 1 through 121.")
    if isinstance(max_payload_bytes, bool) or not isinstance(max_payload_bytes, int) or not 1024 <= max_payload_bytes <= DEFAULT_MAX_PAYLOAD_BYTES:
        raise _error(422, "Evidence payload budget must be between 1 KiB and 10 MiB.")
    capture = captures.capture_context(capture_id)
    if capture.modality == "video" and max_frames < 2:
        raise _error(422, "Video evidence requires a frame budget of at least two to retain both window boundaries.")
    manifest = captures.manifest(capture_id)
    requested = capture.allowedInterval if window is None else TimeInterval.model_validate(window)
    crop = FULL_IMAGE if crop is None else ImageRegion.model_validate(crop)
    if requested.startSeconds < capture.allowedInterval.startSeconds or requested.endSeconds > capture.allowedInterval.endSeconds:
        raise _error(422, "Reinspection window must remain inside the permitted immutable prefix.")
    if capture.modality == "snapshot":
        if requested.startSeconds != 0 or requested.endSeconds != 0:
            raise _error(422, "A snapshot supports only the zero-time evidence window.")
    elif requested.endSeconds <= requested.startSeconds:
        raise _error(422, "Video reinspection requires a nonempty half-open time interval.")
    # Narrowing never grants access to later source bytes or widens the original job context.
    context = capture.model_copy(update={"allowedInterval": requested}, deep=True)
    context = CaptureContext.model_validate(context.model_dump())
    media = [(index, item) for index, item in enumerate(manifest["media"]) if item["partition"] == "prefix"]
    if not media:
        raise _error(422, "Capture has no permitted prefix media.")
    if sum(item["byteLength"] for _, item in media) > MAX_SOURCE_BYTES:
        raise _error(413, "Permitted source bytes exceed the evidence decoding budget.")
    if capture.modality == "video":
        audited_duration = math.fsum(item["auditedDurationSeconds"] for _, item in media)
        if not math.isfinite(audited_duration) or not 0 < audited_duration <= 120 + 1e-9:
            raise _error(422, "Decoded permitted source duration exceeds the evidence duration budget.")
    with tempfile.TemporaryDirectory(prefix="element119-evidence-") as directory_name:
        directory = Path(directory_name)
        segments: list[_Segment] = []
        eligible: list[_DecodedFrame] = []
        decoded_total = 0
        for media_index, item in media:
            # This is the only entry point for source bytes. Never use item['path'].
            data, mime = captures.read_prefix(capture_id, media_index)
            if len(data) != item["byteLength"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise _error(409, "Immutable prefix bytes failed their evidence integrity audit.")
            snapshot = capture.modality == "snapshot"
            path = directory / f"source-{media_index}.{'image' if snapshot else 'ts'}"
            path.write_bytes(data)
            probe = await _probe(path, snapshot=snapshot)
            pts = probe["pts"]
            if len(pts) != item["frameCount"] or decoded_total + len(pts) > MAX_DECODED_FRAMES:
                raise _error(502, "Decoded frame count differs from the immutable capture audit.")
            segment_index = len(segments)
            segments.append(_Segment(media_index, path, data if snapshot else None, mime,
                                     probe["width"], probe["height"], pts[0], len(pts)))
            for local_index, timestamp in enumerate(pts):
                relative = 0.0 if snapshot else item["startSeconds"] + timestamp - pts[0]
                relative = round(relative, 9)
                # Clock drift cannot silently put a later segment's frames in an earlier interval.
                if not snapshot and not item["startSeconds"] - 1e-9 <= relative < item["endSeconds"]:
                    raise _error(422, "Decoded frame timing overlaps a declared segment boundary; source timing needs review.")
                if snapshot or requested.startSeconds <= relative < requested.endSeconds:
                    eligible.append(_DecodedFrame(segment_index, local_index, decoded_total + local_index,
                                                  relative, timestamp))
            decoded_total += len(pts)
        if not eligible:
            raise _error(422, "No decoded source frame lies inside the requested evidence window.")
        if any(second.time_seconds <= first.time_seconds for first, second in zip(eligible, eligible[1:])):
            raise _error(422, "Source frame timestamps overlap; temporal evidence cannot be ordered reliably.")
        chosen, spacing = _sample(eligible, max_frames)
        produced: dict[_DecodedFrame, EvidenceFrame] = {}
        encoded_bytes = 0
        for segment_index, segment in enumerate(segments):
            selected = [frame for frame in chosen if frame.segment_index == segment_index]
            if not selected:
                continue
            x, y, width, height, actual_region = _crop_pixels(crop, segment.width, segment.height)
            if capture.modality == "snapshot" and crop == FULL_IMAGE:
                outputs = [segment.data]
                output_mime = segment.mime
            else:
                expression = "+".join(f"eq(n\\,{frame.local_index})" for frame in selected)
                filters = f"select={expression},format=yuv444p,crop={width}:{height}:{x}:{y}:exact=1"
                raw_images = await _run(["ffmpeg", "-hide_banner", "-v", "error", "-protocol_whitelist", "file,pipe",
                            "-noautorotate", "-i", str(segment.path), "-map", "0:v:0", "-an", "-sn",
                            "-vf", filters, "-fps_mode", "passthrough", "-q:v", "3", "-threads", "1",
                            "-c:v", "mjpeg", "-f", "image2pipe", "pipe:1"],
                            output_limit=max(1, (max_payload_bytes - encoded_bytes) * 3 // 4))
                outputs = _jpeg_images(raw_images)
                if len(outputs) != len(selected):
                    raise _error(502, "Extracted image count differs from the selected source frames; no partial sampling accepted.")
                output_mime = "image/jpeg"
            for source_frame, image in zip(selected, outputs):
                if not image:
                    raise _error(502, "A decoded evidence image is empty.")
                encoded_bytes += 4 * math.ceil(len(image) / 3)
                if encoded_bytes > max_payload_bytes:
                    raise _error(413, "Prepared image bytes exceed the model payload budget; request a smaller window or crop.")
                produced[source_frame] = EvidenceFrame(
                    data=image, mime_type=output_mime, time_seconds=source_frame.time_seconds,
                    frame_index=source_frame.frame_index, region=actual_region, width=width, height=height,
                    source_media_index=segment.media_index, sha256=hashlib.sha256(image).hexdigest(),
                    capture_id=capture.captureId, original_media_sha256=capture.originalMediaSha256,
                    source_pts_seconds=source_frame.pts_seconds,
                )
        frames = tuple(produced[frame] for frame in chosen)
        gaps = [second.time_seconds - first.time_seconds for first, second in zip(frames, frames[1:])]
        sampling = {
            "provenance": "derived", "method": "measured_pts_frame_subsample" if capture.modality == "video" else "immutable_snapshot",
            "timestampBasis": "segment_start_plus_decoded_pts_minus_first_pts" if capture.modality == "video" else "snapshot_zero",
            "timelineBasis": manifest.get("timelineBasis", "snapshot_zero"),
            "requestedWindow": requested.model_dump(), "requestedCrop": crop.model_dump(),
            "decodedPrefixFrameCount": decoded_total, "eligibleFrameCount": len(eligible), "sampledFrameCount": len(frames),
            "omittedEligibleFrameCount": len(eligible) - len(frames), "targetMaximumFps": 1.0,
            "minimumSpacingSeconds": spacing if capture.modality == "video" else None,
            "includesLastEligibleFrame": True, "lastFrameMayBeCloserThanSpacing": True,
            "maximumSampleGapSeconds": max(gaps) if gaps else None,
            "firstSampleSeconds": frames[0].time_seconds, "lastSampleSeconds": frames[-1].time_seconds,
            "base64ImageBytes": encoded_bytes, "maximumBase64ImageBytes": max_payload_bytes,
            "frameIndexMeaning": "decoded_ordinal_in_concatenated_original_prefix",
            "segmentFirstPtsSeconds": [{"sourceMediaIndex": segment.media_index, "ptsSeconds": segment.first_pts} for segment in segments],
            "supportsTemporalOrdering": capture.modality == "video" and len(frames) > 1,
            "limitations": ["Presentation timestamps are media-relative, not verified camera exposure times.",
                            "Sparse images can miss events between samples; they do not establish trajectories or flow rates."],
        }
        if capture.modality == "snapshot":
            sampling["limitations"].append("A snapshot contains no temporal motion evidence.")
        result = PreparedEvidence(context, frames, sampling)
        if encoded_bytes + len(json.dumps(result.metadata()).encode()) > max_payload_bytes:
            raise _error(413, "Prepared image metadata and bytes exceed the evidence payload budget.")
        return result


async def prepare_evidence(captures: CaptureService, capture_id: str, *, window: TimeInterval | None = None,
                           crop: ImageRegion | None = None, max_frames: int = DEFAULT_MAX_FRAMES,
                           max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES) -> PreparedEvidence:
    """Decode/subsample sealed prefix only; failures never return a truncated success.

    ``max_frames`` transparently coarsens the sample interval across the entire
    permitted window. Selected timestamps and gap statistics disclose that change.
    The final eligible frame is included, so its gap may be shorter than one second.
    """
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        raise _error(503, "ffmpeg and ffprobe are required to prepare camera evidence.")
    job_id = f"prepare_{uuid4().hex}"
    captures.pin(capture_id, job_id)
    try:
        async with asyncio.timeout(90):
            return await _prepare(captures, capture_id, window=window, crop=crop,
                                  max_frames=max_frames, max_payload_bytes=max_payload_bytes)
    except TimeoutError:
        raise _error(504, "Evidence preparation timed out; no partial model input was accepted.") from None
    finally:
        captures.unpin(capture_id, job_id)
