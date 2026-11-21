"""Bounded, immutable camera evidence; capture clocks never imply exposure time."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Awaitable, Callable
from urllib.parse import urljoin
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .feeds import HLS_SOURCES, utc_now
from .contracts import CaptureContext
from .proxy import MAX_MEDIA, MAX_PLAYLIST, open_upstream, validate_url

MAX_EPISODE_SECONDS = 120.0
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{1,95}\Z")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def atomic_json(path: Path, value: object) -> None:
    data = canonical(value)
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def fail(code: int, detail: str) -> HTTPException:
    return HTTPException(code, detail)


async def audit_video(data: bytes, expected_duration: float) -> dict:
    """Decode/count every frame. Reject unavailable tooling and truncated captures."""
    if not shutil.which("ffprobe"):
        raise fail(503, "ffprobe is required to audit continuous capture.")
    with tempfile.TemporaryDirectory(prefix="element119-audit-") as directory:
        path = Path(directory) / "segment.ts"
        path.write_bytes(data)
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=nb_read_frames,duration,width,height:format=duration",
            "-of", "json", str(path), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, errors = await asyncio.wait_for(process.communicate(), timeout=20)
        except (TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.communicate()
            raise
        try:
            value = json.loads(output)
            stream = value["streams"][0]
            measured = float(stream.get("duration") or value["format"]["duration"])
            count = int(stream["nb_read_frames"])
            if (process.returncode or errors.strip() or count <= 0 or not math.isfinite(measured)
                    or abs(measured - expected_duration) > max(0.25, expected_duration * 0.05)):
                raise ValueError()
        except (ValueError, KeyError, IndexError, TypeError):
            raise fail(502, "Video duration/frame audit failed; incomplete media was not retained.") from None
        return {"durationSeconds": measured, "frameCount": count,
                "width": int(stream["width"]), "height": int(stream["height"])}


async def audit_snapshot(data: bytes) -> None:
    if not shutil.which("ffprobe"):
        raise fail(503, "ffprobe is required to audit snapshot capture.")
    with tempfile.TemporaryDirectory(prefix="element119-image-") as directory:
        path = Path(directory) / "snapshot.image"
        path.write_bytes(data)
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=nb_read_frames,width,height", "-of", "json", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, errors = await asyncio.wait_for(process.communicate(), timeout=10)
        except (TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.communicate()
            raise
        try:
            stream = json.loads(output)["streams"][0]
            if process.returncode or errors.strip() or int(stream["nb_read_frames"]) != 1 or min(int(stream["width"]), int(stream["height"])) <= 0:
                raise ValueError()
        except (ValueError, KeyError, IndexError, TypeError):
            raise fail(502, "Snapshot decoding audit failed; incomplete image was not retained.") from None


def parse_playlist(camera_id: str, url: str, text: str) -> dict:
    if not text.lstrip("\ufeff \r\n").startswith("#EXTM3U"):
        raise fail(502, "Capture source did not return an HLS playlist.")
    sequence = 0
    pending_duration = None
    pending_variant = False
    source_date = None
    variants, segments = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                sequence = int(line.split(":", 1)[1])
                if sequence < 0:
                    raise ValueError()
            except ValueError:
                raise fail(502, "Invalid HLS media sequence.") from None
        elif line.startswith("#EXT-X-STREAM-INF:"):
            pending_variant = True
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            try:
                parsed = datetime.fromisoformat(line.split(":", 1)[1].replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError()
                source_date = parsed.astimezone(timezone.utc).isoformat()
            except ValueError:
                raise fail(502, "Invalid source program clock.") from None
        elif line.startswith(("#EXT-X-MAP:", "#EXT-X-BYTERANGE:")) or line == "#EXT-X-DISCONTINUITY":
            raise fail(422, "Capture currently supports continuous MPEG-TS HLS without maps, ranges, or discontinuities.")
        elif line.startswith("#EXT-X-KEY:") and "METHOD=NONE" not in line:
            raise fail(422, "Encrypted HLS capture is not supported.")
        elif line.startswith("#EXTINF:"):
            try:
                pending_duration = float(line.split(":", 1)[1].split(",", 1)[0])
                if not math.isfinite(pending_duration) or not 0 < pending_duration <= MAX_EPISODE_SECONDS:
                    raise ValueError()
            except ValueError:
                raise fail(502, "Invalid HLS segment duration.") from None
        elif line and not line.startswith("#"):
            target = validate_url(camera_id, urljoin(url, line))
            if pending_variant:
                variants.append(target)
                pending_variant = False
            elif pending_duration is not None:
                segments.append({"sequence": sequence, "url": target, "durationSeconds": pending_duration,
                                 "sourceCapturedAt": source_date})
                sequence += 1
                if source_date:
                    from datetime import timedelta
                    source_date = (datetime.fromisoformat(source_date) + timedelta(seconds=pending_duration)).isoformat()
                pending_duration = None
            else:
                raise fail(502, "HLS segment is missing its duration.")
    if pending_duration is not None or pending_variant or not (variants or segments):
        raise fail(502, "Incomplete HLS playlist.")
    return {"variants": variants, "segments": segments}


class CaptureService:
    def __init__(self, root: str | Path, client: httpx.AsyncClient, *,
                 max_bytes: int = 1024 * 1024 * 1024, ring_seconds: float = 180,
                 auditor: Callable[[bytes, float], Awaitable[dict]] = audit_video,
                 snapshot_loader: Callable[[], Awaitable[list[dict]]] | None = None):
        if max_bytes <= 0 or not math.isfinite(ring_seconds) or ring_seconds < 120:
            raise ValueError("Invalid capture storage limits")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("rings", "episodes"):
            (self.root / name).mkdir(exist_ok=True)
        self.client, self.max_bytes, self.ring_seconds = client, max_bytes, ring_seconds
        self.auditor, self.snapshot_loader = auditor, snapshot_loader
        self.tasks: dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()
        pins_path = self.root / "pins.json"
        self.pins: dict[str, list[str]] = json.loads(pins_path.read_text()) if pins_path.exists() else {}

    def _path(self, category: str, identifier: str) -> Path:
        if not _ID.fullmatch(identifier):
            raise fail(422, "Malformed capture or source ID.")
        return self.root / category / identifier

    def _load_ring(self, source_id: str) -> dict:
        path = self._path("rings", source_id) / "index.json"
        return json.loads(path.read_text()) if path.exists() else {
            "sourceId": source_id, "recordingId": f"rec_{uuid4().hex}", "segments": [],
            "state": "unverified", "checkedAt": None, "error": None,
        }

    def _save_ring(self, ring: dict) -> None:
        directory = self._path("rings", ring["sourceId"])
        directory.mkdir(exist_ok=True)
        atomic_json(directory / "index.json", ring)

    def _usage(self) -> int:
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())

    def _reserve(self, size: int) -> None:
        # Accepted captures are pinned. Only explicitly unpinned episodes may be removed.
        if self._usage() + size <= self.max_bytes:
            return
        candidates = sorted((self.root / "episodes").iterdir(), key=lambda item: item.stat().st_mtime)
        for directory in candidates:
            if directory.is_dir() and not self.pins.get(directory.name):
                shutil.rmtree(directory)
                if self._usage() + size <= self.max_bytes:
                    return
        raise fail(507, "Capture storage budget is full; pinned evidence has been preserved.")

    async def _fetch(self, source_id: str, url: str, limit: int) -> tuple[bytes, str]:
        response = None
        try:
            response, resolved_url = await open_upstream(self.client, source_id, url, {"Accept-Encoding": "identity"})
            if response.status_code != 200:
                raise fail(502, "Partial camera media was rejected.")
            if response.headers.get("content-encoding", "identity").lower() not in {"identity", ""}:
                raise fail(502, "Unexpectedly encoded camera media was rejected.")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise fail(502, "Camera media exceeded the capture byte limit.")
            declared = response.headers.get("content-length")
            if not data or (declared is not None and int(declared) != len(data)):
                raise fail(502, "Incomplete camera media was rejected.")
            return bytes(data), resolved_url
        except (httpx.HTTPError, ValueError):
            raise fail(502, "Camera capture could not fetch complete media.") from None
        finally:
            if response is not None:
                await response.aclose()

    async def poll_once(self, source_id: str) -> dict:
        source = HLS_SOURCES.get(source_id)
        if not source:
            raise fail(404, "Unknown continuous camera source.")
        url = source["_upstream"]
        for depth in range(3):
            body, resolved_url = await self._fetch(source_id, url, MAX_PLAYLIST)
            try:
                playlist = parse_playlist(source_id, resolved_url, body.decode("utf-8-sig"))
            except UnicodeDecodeError:
                raise fail(502, "Unreadable HLS playlist.") from None
            if playlist["segments"]:
                break
            url = playlist["variants"][0]
        else:
            raise fail(502, "HLS variant nesting exceeded the capture limit.")
        for item in playlist["segments"]:
            async with self.lock:
                ring = self._load_ring(source_id)
                prior = ring["segments"][-1] if ring["segments"] else None
                if prior and item["sequence"] <= prior["sequence"]:
                    continue
                if prior and item["sequence"] != prior["sequence"] + 1:
                    raise fail(409, "Source history has a gap. Stop and restart recording before a new episode.")
            media, _ = await self._fetch(source_id, item["url"], MAX_MEDIA)
            audit = await self.auditor(media, item["durationSeconds"])
            async with self.lock:
                ring = self._load_ring(source_id)
                prior = ring["segments"][-1] if ring["segments"] else None
                if prior and item["sequence"] <= prior["sequence"]:
                    continue
                start = prior["endSeconds"] if prior else 0.0
                entry = {key: value for key, value in item.items() if key != "url"}
                entry.update({"sha256": digest(media), "byteLength": len(media), "receivedAt": utc_now(),
                              "startSeconds": start, "endSeconds": start + item["durationSeconds"],
                              "frameCount": audit["frameCount"], "auditedDurationSeconds": audit["durationSeconds"]})
                directory = self._path("rings", source_id)
                directory.mkdir(exist_ok=True)
                # Evict ring bytes before admitting the next segment; episode bytes are independent copies.
                while ring["segments"] and entry["endSeconds"] - ring["segments"][0]["startSeconds"] > self.ring_seconds:
                    removed = ring["segments"].pop(0)
                    if not any(x["sha256"] == removed["sha256"] for x in ring["segments"]):
                        (directory / (removed["sha256"] + ".ts")).unlink(missing_ok=True)
                # Persist evictions even if the subsequent storage reservation fails.
                self._save_ring(ring)
                self._reserve(len(media) + 4096)
                (directory / (entry["sha256"] + ".ts")).write_bytes(media)
                ring["segments"].append(entry)
                ring.update(state="connected", checkedAt=utc_now(), error=None)
                self._save_ring(ring)
        return self.history(source_id)

    def history(self, source_id: str) -> dict:
        if source_id not in HLS_SOURCES:
            raise fail(404, "Unknown continuous camera source.")
        ring = self._load_ring(source_id)
        segments = ring["segments"]
        state = ring["state"]
        if segments and state == "connected":
            age = time.time() - datetime.fromisoformat(segments[-1]["receivedAt"]).timestamp()
            if age > max(30, segments[-1]["durationSeconds"] * 3):
                state = "stale"
        return {"sourceId": source_id, "recordingId": ring["recordingId"], "modality": "video",
                "state": state, "recording": source_id in self.tasks and not self.tasks[source_id].done(),
                "earliestSeconds": segments[0]["startSeconds"] if segments else None,
                "latestSeconds": segments[-1]["endSeconds"] if segments else None,
                "segmentBoundaries": ([segments[0]["startSeconds"]] + [x["endSeconds"] for x in segments]) if segments else [],
                "clock": "recording_relative", "sourceClock": "program_date_time" if segments and segments[0]["sourceCapturedAt"] else "unknown",
                "clockUncertaintySeconds": None, "error": ring["error"],
                "notice": "Only buffered segment boundaries are selectable. Program time is not verified exposure time."}

    async def start(self, source_id: str) -> dict:
        if source_id not in HLS_SOURCES:
            raise fail(404, "Unknown continuous camera source.")
        if source_id not in self.tasks or self.tasks[source_id].done():
            async with self.lock:
                ring = self._load_ring(source_id)
                # A new recording may begin after a missed sequence; sealed episodes
                # have independent bytes and survive clearing the bounded ring.
                if ring["state"] == "unavailable":
                    shutil.rmtree(self._path("rings", source_id), ignore_errors=True)
                    ring = self._load_ring(source_id)
                self._save_ring(ring)
            async def loop():
                while True:
                    try:
                        await self.poll_once(source_id)
                    except asyncio.CancelledError:
                        raise
                    except (HTTPException, TimeoutError, OSError):
                        async with self.lock:
                            ring = self._load_ring(source_id)
                            ring.update(state="unavailable", checkedAt=utc_now(), error="Recording paused after an upstream or audit failure.")
                            self._save_ring(ring)
                        return
                    await asyncio.sleep(2)
            self.tasks[source_id] = asyncio.create_task(loop())
        return self.history(source_id)

    async def stop(self, source_id: str) -> dict:
        task = self.tasks.pop(source_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return self.history(source_id)

    async def close(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()

    def manifest(self, capture_id: str) -> dict:
        path = self._path("episodes", capture_id) / "manifest.json"
        if not path.is_file():
            raise fail(404, "Capture was not found.")
        manifest = json.loads(path.read_text())
        stored_hash = manifest.pop("manifestSha256")
        if digest(canonical(manifest)) != stored_hash:
            raise fail(409, "Capture manifest integrity check failed.")
        manifest["manifestSha256"] = stored_hash
        return manifest

    def capture_context(self, capture_id: str) -> CaptureContext:
        """Only the allowed source interval crosses into model jobs; no suffix facts."""
        manifest = self.manifest(capture_id)
        fields = {name: manifest[name] for name in CaptureContext.model_fields}
        for name in ("receivedAt", "sourceCapturedAt"):
            if fields[name] is not None:
                fields[name] = datetime.fromisoformat(fields[name].replace("Z", "+00:00")).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return CaptureContext.model_validate(fields)

    def pin(self, capture_id: str, job_id: str) -> None:
        self.manifest(capture_id)
        if not _ID.fullmatch(job_id):
            raise fail(422, "Malformed job ID.")
        self.pins[capture_id] = sorted(set(self.pins.get(capture_id, [])) | {job_id})
        atomic_json(self.root / "pins.json", self.pins)

    def unpin(self, capture_id: str, job_id: str) -> None:
        self.pins[capture_id] = [x for x in self.pins.get(capture_id, []) if x != job_id]
        atomic_json(self.root / "pins.json", self.pins)

    def _persist_episode(self, manifest: dict, blobs: list[tuple[str, bytes]]) -> dict:
        # Hash exact source bytes in capture order. Per-file lengths/hashes below
        # retain boundaries; the separate manifest hash binds those to provenance.
        original_hash = hashlib.sha256()
        for _, data in blobs:
            original_hash.update(data)
        manifest["originalMediaSha256"] = original_hash.hexdigest()
        manifest["manifestSha256"] = digest(canonical(manifest))
        self._reserve(sum(len(data) for _, data in blobs) + len(canonical(manifest)) + 4096)
        directory = self._path("episodes", manifest["captureId"])
        directory.mkdir()
        try:
            for relative, data in blobs:
                target = directory / relative
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(data)
            atomic_json(directory / "manifest.json", manifest)
            self.pin(manifest["captureId"], "capture-retention")
        except BaseException:
            shutil.rmtree(directory)
            raise
        return manifest

    async def create_episode(self, source_id: str, start: float, end: float, *,
                             split: float | None = None, scene_id: str | None = None,
                             recording_id: str | None = None) -> dict:
        if source_id not in HLS_SOURCES:
            raise fail(404, "Unknown continuous camera source.")
        if scene_id is not None and scene_id != source_id:
            raise fail(422, "Source/scene mismatch: capture location is the selected source location.")
        if not all(math.isfinite(x) for x in (start, end)) or not 0 < end - start <= MAX_EPISODE_SECONDS:
            raise fail(422, "Episode source duration must be greater than zero and at most 120 seconds.")
        if split is not None and (not math.isfinite(split) or not start < split < end):
            raise fail(422, "Held-out split must be inside the episode.")
        async with self.lock:
            ring = self._load_ring(source_id)
            if recording_id is not None and recording_id != ring["recordingId"]:
                raise fail(409, "Recording identity changed; refresh available history.")
            selected = [x for x in ring["segments"] if x["startSeconds"] >= start and x["endSeconds"] <= end]
            if not selected or selected[0]["startSeconds"] != start or selected[-1]["endSeconds"] != end:
                raise fail(409, "Requested interval is unavailable; choose existing segment boundaries.")
            audited_duration = math.fsum(item["auditedDurationSeconds"] for item in selected)
            if not math.isfinite(audited_duration) or audited_duration > MAX_EPISODE_SECONDS + 1e-9:
                raise fail(422, "Decoded original media duration exceeds the 120-second episode limit.")
            if split is not None and split not in [x["endSeconds"] for x in selected[:-1]]:
                raise fail(422, "Held-out split must be an existing segment boundary; no hidden trimming is performed.")
            items, blobs = [], []
            for index, segment in enumerate(selected):
                partition = "suffix" if split is not None and segment["startSeconds"] >= split else "prefix"
                path = f"{partition}/{index:04d}.ts"
                data = (self._path("rings", source_id) / (segment["sha256"] + ".ts")).read_bytes()
                if digest(data) != segment["sha256"] or len(data) != segment["byteLength"]:
                    raise fail(409, "Buffered source media failed its integrity check.")
                items.append({**segment, "path": path, "partition": partition,
                              "startSeconds": segment["startSeconds"] - start, "endSeconds": segment["endSeconds"] - start})
                blobs.append((path, data))
            source = HLS_SOURCES[source_id]
            manifest = {"schemaVersion": "1.0", "captureId": f"cap_{uuid4().hex}", "sourceId": source_id,
                        "sceneId": source_id, "recordingId": ring["recordingId"], "sourceUrl": source["sourceUrl"],
                        "coordinates": source["coordinates"], "modality": "video", "provenance": "observed",
                        "durationSeconds": end - start, "allowedInterval": {"startSeconds": 0, "endSeconds": (split or end) - start},
                        "auditedDurationSeconds": audited_duration, "timelineBasis": "hls_declared_segment_duration",
                        "auditedPrefixDurationSeconds": math.fsum(item["auditedDurationSeconds"] for item in items if item["partition"] == "prefix"),
                        "receivedAt": selected[-1]["receivedAt"], "sourceCapturedAt": selected[0]["sourceCapturedAt"],
                        "sourceClock": "program_date_time" if selected[0]["sourceCapturedAt"] else "unknown",
                        "clockUncertaintySeconds": None, "createdAt": utc_now(), "media": items,
                        "frameCount": sum(x["frameCount"] for x in items), "byteLength": sum(x["byteLength"] for x in items),
                        "motionClaimsAllowed": True, "cadence": {"kind": "continuous_video", "uniqueFramesPerSecond": None}}
            return self._persist_episode(manifest, blobs)

    async def capture_snapshot(self, source_id: str, scene_id: str | None = None) -> dict:
        if not _ID.fullmatch(source_id) or scene_id not in (None, source_id):
            raise fail(422, "Malformed source ID or source/scene mismatch.")
        if self.snapshot_loader is None:
            raise fail(503, "Snapshot registry is unavailable.")
        sources = await self.snapshot_loader()
        source = next((row for row in sources if row["id"] == source_id and row["kind"] == "snapshot"), None)
        if source is None:
            raise fail(404, "Unknown snapshot source.")
        # Use only the official camera-ID endpoint; no client or directory URL is fetched.
        try:
            async with self.client.stream("GET", f"https://webcams.nyctmc.org/api/cameras/{source_id}/image", follow_redirects=False) as response:
                if response.status_code != 200 or response.headers.get("content-type", "").split(";")[0] not in {"image/jpeg", "image/png"}:
                    raise fail(502, "Snapshot source returned no supported image.")
                if response.headers.get("content-encoding", "identity").lower() not in {"identity", ""}:
                    raise fail(502, "Unexpectedly encoded snapshot was rejected.")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_MEDIA:
                        raise fail(502, "Snapshot exceeds the capture byte limit.")
                declared = response.headers.get("content-length")
                if declared is not None and int(declared) != len(data):
                    raise fail(502, "Incomplete snapshot was rejected.")
                mime = response.headers["content-type"].split(";")[0]
        except (httpx.HTTPError, ValueError):
            raise fail(502, "Snapshot capture could not fetch complete media.") from None
        image_data = bytes(data)
        if not ((mime == "image/jpeg" and image_data.startswith(b"\xff\xd8") and image_data.endswith(b"\xff\xd9")) or
                (mime == "image/png" and image_data.startswith(b"\x89PNG\r\n\x1a\n") and image_data.endswith(b"IEND\xaeB`\x82"))):
            raise fail(502, "Snapshot image framing audit failed.")
        await audit_snapshot(image_data)
        received = utc_now()
        async with self.lock:
            previous_path = self._path("rings", source_id) / "snapshot.json"
            previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
            sha = digest(image_data)
            repeated = previous is not None and previous["sha256"] == sha
            manifest = {"schemaVersion": "1.0", "captureId": f"cap_{uuid4().hex}", "sourceId": source_id,
                        "sceneId": source_id, "sourceUrl": source["sourceUrl"], "coordinates": source["coordinates"],
                        "modality": "snapshot", "provenance": "observed", "durationSeconds": 0,
                        "allowedInterval": {"startSeconds": 0, "endSeconds": 0}, "receivedAt": received,
                        "sourceCapturedAt": None, "sourceClock": "unknown", "clockUncertaintySeconds": None,
                        "createdAt": received, "motionClaimsAllowed": False, "frameCount": 1, "byteLength": len(image_data),
                        "cadence": {"kind": "periodic_snapshot", "repeated": repeated, "uniqueFramesPerSecond": None,
                                    "previousReceivedAt": previous["receivedAt"] if previous else None},
                        "media": [{"path": "prefix/0000.image", "partition": "prefix", "sha256": sha,
                                   "byteLength": len(image_data), "mimeType": mime, "startSeconds": 0, "endSeconds": 0,
                                   "receivedAt": received, "frameCount": 1}]}
            result = self._persist_episode(manifest, [("prefix/0000.image", image_data)])
            previous_path.parent.mkdir(exist_ok=True)
            atomic_json(previous_path, {"sha256": sha, "receivedAt": received})
            return result

    def read_prefix(self, capture_id: str, media_index: int) -> tuple[bytes, str]:
        manifest = self.manifest(capture_id)
        if media_index < 0 or media_index >= len(manifest["media"]):
            raise fail(404, "Capture media was not found.")
        item = manifest["media"][media_index]
        if item["partition"] != "prefix":
            raise fail(403, "Held-out suffix media is not available through inference routes.")
        data = (self._path("episodes", capture_id) / item["path"]).read_bytes()
        if digest(data) != item["sha256"] or len(data) != item["byteLength"]:
            raise fail(409, "Capture media integrity check failed.")
        return data, item.get("mimeType", "video/mp2t")


class EpisodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    sourceId: str
    sceneId: str | None = None
    recordingId: str
    startSeconds: float = Field(ge=0)
    endSeconds: float = Field(gt=0)
    splitSeconds: float | None = None


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sourceId: str
    sceneId: str | None = None


router = APIRouter(prefix="/api/captures", tags=["capture"])


def service(request: Request) -> CaptureService:
    return request.app.state.capture


@router.post("/sources/{source_id}/start")
async def start_source(source_id: str, request: Request):
    return await service(request).start(source_id)


@router.post("/sources/{source_id}/stop")
async def stop_source(source_id: str, request: Request):
    return await service(request).stop(source_id)


@router.get("/sources/{source_id}/history")
async def source_history(source_id: str, request: Request):
    return service(request).history(source_id)


@router.post("/episodes", status_code=201)
async def create_episode(body: EpisodeRequest, request: Request):
    return await service(request).create_episode(body.sourceId, body.startSeconds, body.endSeconds,
        split=body.splitSeconds, scene_id=body.sceneId, recording_id=body.recordingId)


@router.post("/snapshots", status_code=201)
async def capture_snapshot(body: SnapshotRequest, request: Request):
    return await service(request).capture_snapshot(body.sourceId, body.sceneId)


@router.get("/episodes/{capture_id}")
async def get_episode(capture_id: str, request: Request):
    return service(request).manifest(capture_id)


@router.get("/episodes/{capture_id}/media/{media_index}")
async def get_media(capture_id: str, media_index: int, request: Request):
    data, mime = service(request).read_prefix(capture_id, media_index)
    return Response(data, media_type=mime, headers={"Cache-Control": "private, immutable", "X-Content-Type-Options": "nosniff"})
