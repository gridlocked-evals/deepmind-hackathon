"""Bounded camera recording and immutable capture episodes (issue #4).

- ``HlsRecorder`` keeps a bounded in-memory ring of recent HLS media segments
  for one registered 511NY camera. A camera that was never recorded has no
  history: "30 seconds ago" is offered only inside the buffered window.
- ``CaptureStore`` turns a buffered interval (or one DOT snapshot) into an
  episode on disk: a model-input ``prefix`` file, an optional physically
  separate ``holdout`` file, and a read-only ``manifest.json`` with exact
  bytes, hashes, and both clocks (source-reported time, when the stream
  carries one, and our server receipt time).
- Episodes are immutable. Inference retries re-read the same bytes (and the
  hashes are re-verified); capturing again always creates a new episode.
- Pins keep media referenced by active jobs. Cleanup enforces a storage
  budget on unpinned episodes only, and the index is rebuilt from disk on
  restart.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
from typing import Awaitable, Callable
from urllib.parse import urljoin

from .contracts import MAX_EVIDENCE_SECONDS, Capture, Unknown
from .feeds import HLS_SOURCES

MANIFEST_VERSION = "capture-manifest.v1"
MAX_SEGMENT_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
DEFAULT_RING_SECONDS = 180.0
DEFAULT_RING_BYTES = 96 * 1024 * 1024
DEFAULT_BUDGET_BYTES = 2 * 1024 * 1024 * 1024
RECORDER_IDLE_SECONDS = 600.0

Fetch = Callable[[str], Awaitable[tuple[bytes, str]]]
Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class CaptureError(Exception):
    """A capture request that cannot be satisfied truthfully."""
    status = 400

    def __init__(self, message: str, **detail):
        super().__init__(message)
        self.detail = {"error": message, **detail}


class HistoryUnavailable(CaptureError):
    status = 409


class TruncatedCapture(CaptureError):
    status = 422


class SceneMismatch(CaptureError):
    status = 409


class AccessDenied(CaptureError):
    status = 403


# --------------------------------------------------------------- HLS parsing

@dataclass(frozen=True)
class PlaylistSegment:
    sequence: int
    duration: float
    uri: str
    program_time: datetime | None
    discontinuity: bool


@dataclass(frozen=True)
class MediaPlaylist:
    target_duration: float
    media_sequence: int
    segments: list[PlaylistSegment]
    variant_uri: str | None = None
    ended: bool = False


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def parse_playlist(text: str, base_url: str) -> MediaPlaylist:
    """Parse a master (first variant only) or media playlist."""
    lines = [line.strip() for line in text.lstrip("﻿").splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise CaptureError("The camera did not return an HLS playlist.")
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
            return MediaPlaylist(0, 0, [], variant_uri=urljoin(base_url, lines[i + 1]))
        if line.startswith("#EXT-X-MAP"):
            raise CaptureError("Fragmented-MP4 HLS (EXT-X-MAP) is not supported by the recorder.")
        if line.startswith("#EXT-X-KEY") and "METHOD=NONE" not in line:
            raise CaptureError("Encrypted HLS media is not supported by the recorder.")
    target, sequence, segments = 0.0, 0, []
    duration: float | None = None
    program_time: datetime | None = None
    discontinuity = False
    ended = False
    for line in lines[1:]:
        if line.startswith("#EXT-X-TARGETDURATION:"):
            target = float(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            sequence = int(line.split(":", 1)[1])
        elif line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",", 1)[0])
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            program_time = _parse_time(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-DISCONTINUITY") and not line.startswith("#EXT-X-DISCONTINUITY-SEQUENCE"):
            discontinuity = True
        elif line.startswith("#EXT-X-ENDLIST"):
            ended = True
        elif not line.startswith("#"):
            if duration is None:
                raise CaptureError("HLS segment without EXTINF duration.")
            seq = sequence + len(segments)
            segments.append(PlaylistSegment(seq, duration, urljoin(base_url, line), program_time, discontinuity))
            # A program date applies to its segment; later ones advance by duration.
            program_time = program_time + timedelta(seconds=duration) if program_time else None
            duration, discontinuity = None, False
    return MediaPlaylist(target, sequence, segments, ended=ended)


# ------------------------------------------------------------------ recorder

@dataclass
class Segment:
    sequence: int
    duration: float
    data: bytes
    sha: str
    received_at: datetime
    program_time: datetime | None
    discontinuity: bool
    repeated: bool  # identical bytes to the previous segment


@dataclass
class RecorderHealth:
    started_at: datetime
    last_attempt: datetime | None = None
    last_success: datetime | None = None
    last_new_segment: datetime | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    target_duration: float = 0.0


class HlsRecorder:
    """Bounded ring of recent segments for one registered camera."""

    def __init__(self, camera_id: str, fetch: Fetch, clock: Clock = utcnow,
                 max_seconds: float = DEFAULT_RING_SECONDS, max_bytes: int = DEFAULT_RING_BYTES):
        if camera_id not in HLS_SOURCES:
            raise CaptureError("Unknown camera source.", cameraId=camera_id)
        self.camera_id = camera_id
        self.fetch = fetch
        self.clock = clock
        self.max_seconds = max(max_seconds, MAX_EVIDENCE_SECONDS)
        self.max_bytes = max_bytes
        self.segments: deque[Segment] = deque()
        self.bytes = 0
        self.last_sequence: int | None = None
        self.media_url: str | None = None
        self.health = RecorderHealth(started_at=clock())
        self.touched = time.monotonic()
        self.lock = asyncio.Lock()

    async def poll_once(self) -> int:
        """Fetch the playlist and any new segments. Returns segments added."""
        self.health.last_attempt = self.clock()
        try:
            url = self.media_url or HLS_SOURCES[self.camera_id]["_upstream"]
            data, final = await self.fetch(url)
            playlist = parse_playlist(data.decode("utf-8-sig"), final)
            if playlist.variant_uri:
                self.media_url = playlist.variant_uri
                data, final = await self.fetch(self.media_url)
                playlist = parse_playlist(data.decode("utf-8-sig"), final)
            self.health.target_duration = playlist.target_duration
            added = 0
            for item in playlist.segments:
                if self.last_sequence is not None and item.sequence <= self.last_sequence:
                    continue
                gap = self.last_sequence is not None and item.sequence != self.last_sequence + 1
                body, _ = await self.fetch(item.uri)
                if len(body) > MAX_SEGMENT_BYTES:
                    raise CaptureError("Camera segment exceeded the byte limit.")
                await self._append(item, body, discontinuity=item.discontinuity or gap)
                added += 1
            self.health.last_success = self.clock()
            self.health.consecutive_failures = 0
            self.health.last_error = None
            if added:
                self.health.last_new_segment = self.clock()
            return added
        except Exception as exc:  # recorded, never raised into the poll loop
            self.health.consecutive_failures += 1
            self.health.last_error = str(exc) or exc.__class__.__name__
            return 0

    async def _append(self, item: PlaylistSegment, body: bytes, discontinuity: bool):
        async with self.lock:
            digest = sha256(body)
            previous = self.segments[-1] if self.segments else None
            self.segments.append(Segment(item.sequence, item.duration, body, digest, self.clock(),
                                         item.program_time, discontinuity,
                                         repeated=bool(previous and previous.sha == digest)))
            self.bytes += len(body)
            self.last_sequence = item.sequence
            while self.segments and (self.buffered_seconds() > self.max_seconds or self.bytes > self.max_bytes):
                self.bytes -= len(self.segments.popleft().data)

    def buffered_seconds(self) -> float:
        return sum(s.duration for s in self.segments)

    def availability(self) -> str:
        h = self.health
        if h.consecutive_failures and (not h.last_success or h.consecutive_failures >= 3):
            return "unavailable"
        if not self.segments:
            return "starting"
        stale_after = max(3 * (h.target_duration or 2.0), 10.0)
        if h.last_new_segment and (self.clock() - h.last_new_segment).total_seconds() > stale_after:
            return "stale"
        return "recording"

    def status(self) -> dict:
        self.touched = time.monotonic()
        segs = list(self.segments)
        repeated = sum(s.repeated for s in segs)
        return {
            "cameraId": self.camera_id,
            "coordinates": HLS_SOURCES[self.camera_id]["coordinates"],
            "availability": self.availability(),
            "recordingSince": self.health.started_at.isoformat(),
            "bufferedSeconds": round(self.buffered_seconds(), 3),
            "segments": len(segs),
            "earliestReceivedAt": segs[0].received_at.isoformat() if segs else None,
            "latestReceivedAt": segs[-1].received_at.isoformat() if segs else None,
            "earliestSourceTime": segs[0].program_time.isoformat() if segs and segs[0].program_time else None,
            "sourceClock": "source_reported" if segs and all(s.program_time for s in segs) else "unknown",
            "repeatedSegments": repeated,
            "lastError": self.health.last_error,
            # Offsets measure back from the end of the newest buffered segment.
            "history": {"maxSecondsAgo": round(self.buffered_seconds(), 3), "maxDurationS": MAX_EVIDENCE_SECONDS},
        }

    def select(self, seconds_ago: float, duration_s: float) -> list[Segment]:
        """Whole segments covering [now - seconds_ago, now - seconds_ago + duration]."""
        if duration_s <= 0 or duration_s > MAX_EVIDENCE_SECONDS:
            raise CaptureError(f"Duration must be within (0, {MAX_EVIDENCE_SECONDS:g}] seconds.")
        if seconds_ago < duration_s:
            raise CaptureError("The interval would end in the future; secondsAgo must be at least durationS.")
        segs = list(self.segments)
        total = sum(s.duration for s in segs)
        if seconds_ago > total + 1e-6:
            raise HistoryUnavailable("Requested history is not buffered for this camera.",
                                     availableSecondsAgo=round(total, 3), cameraId=self.camera_id)
        start, end = total - seconds_ago, total - seconds_ago + duration_s
        chosen, cursor = [], 0.0
        for s in segs:
            mid = cursor + s.duration / 2
            if start - 1e-6 <= mid <= end + 1e-6:
                chosen.append(s)
            cursor += s.duration
        return chosen


# --------------------------------------------------------------- probing

def probe_media(path: Path) -> dict | None:
    """Frame count, size and duration via ffprobe, or None if unavailable."""
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
             "-show_entries", "stream=width,height,nb_read_packets:format=duration", "-of", "json", str(path)],
            capture_output=True, timeout=60, check=True, text=True)
        info = json.loads(out.stdout)
        stream = (info.get("streams") or [{}])[0]
        return {"frames": int(stream["nb_read_packets"]), "width": int(stream["width"]),
                "height": int(stream["height"]), "duration": float(info["format"]["duration"])}
    except (subprocess.SubprocessError, OSError, KeyError, ValueError, IndexError, TypeError):
        return None


# ------------------------------------------------------------------- store

_EPISODE_ID = re.compile(r"^cap_[A-Za-z0-9-]{4,64}$")
_JOB_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")


@dataclass
class CaptureStore:
    root: Path
    budget_bytes: int = DEFAULT_BUDGET_BYTES
    clock: Clock = utcnow
    probe: Callable[[Path], dict | None] = probe_media
    _last_snapshot: dict[str, str] = field(default_factory=dict)
    # Guards pins and eviction: episodes are written in to_thread, so a pin can race the check-then-delete.
    # Reentrant because _write_episode holds it across its fit check and enforce_budget. Disk only, no network.
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def __post_init__(self):
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        # Rebuild the index from disk; discard half-written episodes.
        for tmp in self.root.glob(".tmp-*"):
            shutil.rmtree(tmp, ignore_errors=True)

    # -- creation ---------------------------------------------------------

    def create_hls_episode(self, recorder: HlsRecorder, seconds_ago: float, duration_s: float,
                           holdout_s: float = 0.0, scene_camera_id: str | None = None) -> dict:
        if scene_camera_id and scene_camera_id != recorder.camera_id:
            raise SceneMismatch("The selected scene belongs to a different camera.",
                                sceneCameraId=scene_camera_id, cameraId=recorder.camera_id)
        if holdout_s < 0 or holdout_s >= duration_s:
            raise CaptureError("holdoutS must be at least 0 and shorter than durationS.")
        if recorder.availability() == "unavailable":
            raise HistoryUnavailable("The camera is unavailable.", cameraId=recorder.camera_id,
                                     lastError=recorder.health.last_error)
        segments = recorder.select(seconds_ago, duration_s)
        self._audit(segments, duration_s, recorder.health.target_duration)
        split = len(segments)
        if holdout_s > 0:
            acc = 0.0
            for i in range(len(segments) - 1, -1, -1):
                acc += segments[i].duration
                if acc >= holdout_s - 1e-6:
                    split = i
                    break
            if split <= 0 or split == len(segments):
                raise TruncatedCapture("The interval is too short to split into prefix and held-out files.")
        prefix, holdout = segments[:split], segments[split:]
        labels = self._labels(segments, recorder)
        return self._write_episode(
            camera_id=recorder.camera_id, source_kind="hls", media_type="video/mp2t", ext="ts",
            prefix_bytes=b"".join(s.data for s in prefix),
            holdout_bytes=b"".join(s.data for s in holdout) if holdout else None,
            segments={"prefix": [self._seg(s) for s in prefix], "holdout": [self._seg(s) for s in holdout]},
            prefix_duration=sum(s.duration for s in prefix), holdout_duration=sum(s.duration for s in holdout),
            received=(prefix[0].received_at, segments[-1].received_at),
            source_clock="source_reported" if all(s.program_time for s in segments) else "unknown",
            request={"secondsAgo": seconds_ago, "durationS": duration_s, "holdoutS": holdout_s},
            labels=labels)

    def create_snapshot_episode(self, camera_id: str, image: bytes, content_type: str,
                                received_at: datetime, source_url: str, coordinates: list[float] | None = None) -> dict:
        if content_type not in {"image/jpeg", "image/png"}:
            raise CaptureError("The snapshot was not a JPEG or PNG image.")
        if not image or len(image) > MAX_SNAPSHOT_BYTES:
            raise CaptureError("The snapshot was empty or exceeded the size limit.")
        digest = sha256(image)
        labels = ["snapshot_only", "no_motion_evidence", "source_clock_unknown"]
        if self._last_snapshot.get(camera_id) == digest:
            labels.append("repeated_still")
        self._last_snapshot[camera_id] = digest
        return self._write_episode(
            camera_id=camera_id, source_kind="snapshot", media_type=content_type,
            ext="jpg" if content_type == "image/jpeg" else "png",
            prefix_bytes=image, holdout_bytes=None, segments={"prefix": [], "holdout": []},
            prefix_duration=0.0, holdout_duration=0.0, received=(received_at, received_at),
            source_clock="unknown", request={"snapshotUrl": source_url}, labels=labels, coordinates=coordinates)

    def _audit(self, segments: list[Segment], requested: float, target: float):
        if not segments:
            raise TruncatedCapture("No buffered segments fall inside the requested interval.")
        got = sum(s.duration for s in segments)
        tolerance = max(target, max(s.duration for s in segments))
        if got > MAX_EVIDENCE_SECONDS + 1e-6:
            raise CaptureError("Selection exceeds the 120-second evidence cap.", selectedS=round(got, 3))
        if got < requested - tolerance:
            raise TruncatedCapture("Buffered media covers less than the requested duration.",
                                   requestedS=requested, availableS=round(got, 3))
        for a, b in zip(segments, segments[1:]):
            if b.sequence != a.sequence + 1 or b.discontinuity:
                raise TruncatedCapture("The interval contains a gap or discontinuity in the source stream.",
                                       afterSequence=a.sequence, nextSequence=b.sequence)

    @staticmethod
    def _labels(segments: list[Segment], recorder: HlsRecorder) -> list[str]:
        labels = []
        if not all(s.program_time for s in segments):
            labels.append("source_clock_unknown")
        repeated = sum(s.repeated for s in segments)
        if len(segments) > 1 and repeated >= len(segments) - 1:
            labels.append("repeated_stills")
        elif repeated:
            labels.append("repeated_segments")
        if recorder.availability() == "stale":
            labels.append("stale_source")
        return labels

    @staticmethod
    def _seg(s: Segment) -> dict:
        return {"sequence": s.sequence, "durationS": s.duration, "bytes": len(s.data), "sha256": s.sha,
                "receivedAt": s.received_at.isoformat(),
                "sourceTime": s.program_time.isoformat() if s.program_time else None, "repeated": s.repeated}

    def _write_episode(self, *, camera_id, source_kind, media_type, ext, prefix_bytes, holdout_bytes, segments,
                       prefix_duration, holdout_duration, received, source_clock, request, labels,
                       coordinates=None) -> dict:
        episode_id = f"cap_{self.clock().strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}"
        tmp = self.root / f".tmp-{episode_id}"
        (tmp / "prefix").mkdir(parents=True)
        prefix_path = tmp / "prefix" / f"media.{ext}"
        prefix_path.write_bytes(prefix_bytes)
        files = {"prefix": {"path": f"prefix/media.{ext}", "bytes": len(prefix_bytes), "sha256": sha256(prefix_bytes),
                            "durationS": round(prefix_duration, 6)}}
        if holdout_bytes is not None:
            (tmp / "holdout").mkdir()
            (tmp / "holdout" / f"media.{ext}").write_bytes(holdout_bytes)
            files["holdout"] = {"path": f"holdout/media.{ext}", "bytes": len(holdout_bytes),
                                "sha256": sha256(holdout_bytes), "durationS": round(holdout_duration, 6)}
        probe = self.probe(prefix_path) if source_kind == "hls" else None
        unknowns = []
        if source_clock == "unknown":
            unknowns.append({"field": "exposureTime", "reason": "The source does not report capture time; only server receipt time is known."})
        if probe:
            # Reject truncation found by actually decoding, not just by playlist durations.
            if probe["duration"] < prefix_duration - max(1.0, 0.1 * prefix_duration):
                shutil.rmtree(tmp, ignore_errors=True)
                raise TruncatedCapture("Decoded media is shorter than the playlist durations.",
                                       playlistS=round(prefix_duration, 3), decodedS=round(probe["duration"], 3))
        elif source_kind == "hls":
            unknowns.append({"field": "frames", "reason": "ffprobe was unavailable or could not read the media."})
        if source_kind == "snapshot":
            frames = {"frameCount": 1, "width": None, "height": None}
            unknowns.append({"field": "frames", "reason": "Snapshot pixel size is not decoded by the recorder."})
        else:
            frames = {"frameCount": probe["frames"], "width": probe["width"], "height": probe["height"]} if probe else \
                     {"frameCount": None, "width": None, "height": None}
        cadence = None
        if probe and prefix_duration > 0:
            cadence = round(probe["frames"] / prefix_duration, 3)
            if cadence < 5:
                labels = [*labels, "low_effective_cadence"]
        manifest = {
            "manifestVersion": MANIFEST_VERSION,
            "id": episode_id,
            "cameraId": camera_id,
            "coordinates": coordinates or HLS_SOURCES.get(camera_id, {}).get("coordinates"),
            "sourceKind": source_kind,
            "mediaType": media_type,
            "createdAt": self.clock().isoformat(),
            "request": request,
            "clocks": {
                "sourceClock": source_clock,
                "retrievalStartedAt": received[0].isoformat(),
                "retrievalEndedAt": received[1].isoformat(),
                "meaning": "retrieval* = server receipt time; segments[].sourceTime = stream-reported time when present",
            },
            "files": files,
            "segments": segments,
            "totalDurationS": round(prefix_duration + holdout_duration, 6),
            "effectiveFps": cadence,
            **frames,
            "labels": labels,
            "unknowns": unknowns,
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
        for path in tmp.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
        os.replace(tmp, self.root / episode_id)
        with self._lock:
            # Check fit before evicting: evicting and then rejecting this episode anyway would lose data for nothing.
            evictable = sum(self._size(p) for p in self.root.iterdir()
                            if _EPISODE_ID.match(p.name) and p.name != episode_id and not self.pins(p.name))
            if self.usage_bytes() - evictable > self.budget_bytes:
                self._remove(self.root / episode_id)
                raise CaptureError("Capture budget is exhausted by pinned episodes.", budgetBytes=self.budget_bytes)
            self.enforce_budget(protect=episode_id)
        return manifest

    # -- access -----------------------------------------------------------

    def _dir(self, episode_id: str) -> Path:
        if not _EPISODE_ID.match(episode_id or "") or not (self.root / episode_id / "manifest.json").is_file():
            raise CaptureError("Unknown capture episode.", episodeId=episode_id)
        return self.root / episode_id

    def get(self, episode_id: str, verify: bool = False) -> dict:
        directory = self._dir(episode_id)
        manifest = json.loads((directory / "manifest.json").read_text())
        if verify:
            for name, info in manifest["files"].items():
                if sha256((directory / info["path"]).read_bytes()) != info["sha256"]:
                    raise CaptureError("Episode media no longer matches its manifest hash.", file=name)
        return manifest

    def list(self) -> list[dict]:
        return [self.get(p.name) for p in sorted(self.root.iterdir()) if _EPISODE_ID.match(p.name)]

    def model_input(self, episode_id: str, camera_id: str | None = None) -> tuple[Path, str]:
        """Path and hash of the model-visible prefix. Never exposes the holdout.

        Re-verifies bytes so an inference retry always sees the original moment.
        """
        manifest = self.get(episode_id, verify=True)
        if camera_id and manifest["cameraId"] != camera_id:
            raise SceneMismatch("Episode belongs to a different camera.", episodeCameraId=manifest["cameraId"], cameraId=camera_id)
        info = manifest["files"]["prefix"]
        return self._dir(episode_id) / info["path"], info["sha256"]

    def holdout(self, episode_id: str, role: str) -> tuple[Path, str]:
        if role != "evaluation":
            raise AccessDenied("Held-out media is only available to the evaluation role.")
        manifest = self.get(episode_id, verify=True)
        info = manifest["files"].get("holdout")
        if not info:
            raise CaptureError("This episode has no held-out file.")
        return self._dir(episode_id) / info["path"], info["sha256"]

    def capture_contract(self, episode_id: str) -> Capture:
        """The ``element119.v1`` Capture describing the model-visible prefix."""
        m = self.get(episode_id)
        return Capture(
            id=m["id"], cameraId=m["cameraId"], sourceKind=m["sourceKind"],
            mediaSha256=m["files"]["prefix"]["sha256"], mediaType=m["mediaType"],
            durationS=m["files"]["prefix"]["durationS"], frameCount=m["frameCount"],
            width=m["width"], height=m["height"],
            retrievalStartedAt=m["clocks"]["retrievalStartedAt"], retrievalEndedAt=m["clocks"]["retrievalEndedAt"],
            captureClock=m["clocks"]["sourceClock"], unknowns=[Unknown(**u) for u in m["unknowns"]])

    # -- pins and cleanup -------------------------------------------------

    def _pins_path(self, episode_id: str) -> Path:
        return self._dir(episode_id) / "pins.json"

    def pins(self, episode_id: str) -> list[str]:
        path = self._pins_path(episode_id)
        return json.loads(path.read_text()) if path.exists() else []

    def pin(self, episode_id: str, job_id: str) -> list[str]:
        if not _JOB_ID.match(job_id):
            raise CaptureError("Invalid job id.")
        with self._lock:
            pins = sorted(set(self.pins(episode_id)) | {job_id})
            self._write_pins(episode_id, pins)
        return pins

    def unpin(self, episode_id: str, job_id: str) -> list[str]:
        with self._lock:
            pins = [p for p in self.pins(episode_id) if p != job_id]
            self._write_pins(episode_id, pins)
        return pins

    def _write_pins(self, episode_id: str, pins: list[str]):
        path = self._pins_path(episode_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pins))
        os.replace(tmp, path)

    def usage_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())

    @staticmethod
    def _size(path: Path) -> int:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    def _remove(self, path: Path):
        for f in path.rglob("*"):
            f.chmod(0o644) if f.is_file() else None
        shutil.rmtree(path)

    def enforce_budget(self, protect: str | None = None) -> list[str]:
        """Delete the oldest unpinned episodes (never ``protect``) until usage fits the budget."""
        with self._lock:  # the pins() check and the delete must not interleave with pin()
            removed = []
            episodes = sorted((p for p in self.root.iterdir() if _EPISODE_ID.match(p.name)),
                              key=lambda p: json.loads((p / "manifest.json").read_text())["createdAt"])
            usage = self.usage_bytes()
            for path in episodes:
                if usage <= self.budget_bytes:
                    break
                if path.name == protect or self.pins(path.name):
                    continue
                size = self._size(path)
                self._remove(path)
                usage -= size
                removed.append(path.name)
            return removed
