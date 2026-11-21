"""Capture routes (issue #4). See docs/CAMERA_API.md#capture-episodes."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .capture import (RECORDER_IDLE_SECONDS, MAX_SNAPSHOT_BYTES, MAX_SEGMENT_BYTES, CaptureError, CaptureStore,
                      HlsRecorder, utcnow)
from .contracts import MAX_EVIDENCE_SECONDS
from .feeds import HLS_SOURCES
from .proxy import open_upstream

router = APIRouter(prefix="/api/captures")
ROOT = Path(__file__).resolve().parent.parent
DOT_IMAGE = "https://webcams.nyctmc.org/api/cameras/{id}/image"


def make_store() -> CaptureStore:
    root = Path(os.environ.get("CAPTURE_DIR", ROOT / "var" / "captures"))
    budget = int(os.environ.get("CAPTURE_BUDGET_BYTES", 2 * 1024 * 1024 * 1024))
    return CaptureStore(root=root, budget_bytes=budget)


class Recorders:
    """One bounded recorder task per registered HLS camera."""

    def __init__(self):
        self.items: dict[str, tuple[HlsRecorder, asyncio.Task]] = {}

    def get(self, camera_id: str) -> HlsRecorder | None:
        entry = self.items.get(camera_id)
        if entry:
            return entry[0]
        return None

    def start(self, camera_id: str, client: httpx.AsyncClient) -> HlsRecorder:
        existing = self.get(camera_id)
        if existing and not self.items[camera_id][1].done() and existing.availability() != "unavailable":
            return existing
        if existing:
            self.items[camera_id][1].cancel()

        async def fetch(url: str) -> tuple[bytes, str]:
            response, final = await open_upstream(client, camera_id, url, {"Accept-Encoding": "identity"})
            try:
                if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                    raise CaptureError("Camera media was partial or encoded.")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_SEGMENT_BYTES:
                        raise CaptureError("Camera response exceeded the byte limit.")
                length = response.headers.get("content-length")
                if length is not None and int(length) != len(data):
                    raise CaptureError("Camera response was incomplete.")
                return bytes(data), final
            finally:
                await response.aclose()

        recorder = HlsRecorder(camera_id, fetch)
        self.items[camera_id] = (recorder, asyncio.create_task(self._run(recorder)))
        return recorder

    @staticmethod
    async def _run(recorder: HlsRecorder):
        while time.monotonic() - recorder.touched < RECORDER_IDLE_SECONDS:
            await recorder.poll_once()
            await asyncio.sleep(max(1.0, (recorder.health.target_duration or 2.0) / 2))

    async def stop_all(self):
        for _, task in self.items.values():
            task.cancel()
        await asyncio.gather(*(t for _, t in self.items.values()), return_exceptions=True)
        self.items.clear()

    async def stop(self, camera_id: str):
        entry = self.items.get(camera_id)
        if entry:
            entry[1].cancel()
            await asyncio.gather(entry[1], return_exceptions=True)


def _error(exc: CaptureError) -> JSONResponse:
    return JSONResponse(exc.detail, status_code=exc.status)


def _hls(camera_id: str):
    if camera_id not in HLS_SOURCES:
        raise HTTPException(404, "Unknown camera source.")


@router.post("/{camera_id}/recording")
async def start_recording(camera_id: str, request: Request):
    """Begin buffering a fresh camera selection. History starts now."""
    _hls(camera_id)
    recorder = request.app.state.recorders.start(camera_id, request.app.state.http)
    return {**recorder.status(), "recording": True}


@router.get("/{camera_id}/buffer")
async def buffer_status(camera_id: str, request: Request):
    _hls(camera_id)
    recorder = request.app.state.recorders.get(camera_id)
    if not recorder:
        return {"cameraId": camera_id, "availability": "not_recording", "recording": False, "bufferedSeconds": 0,
                "history": {"maxSecondsAgo": 0, "maxDurationS": MAX_EVIDENCE_SECONDS},
                "notice": "No history is recorded for this camera. Start recording to buffer footage."}
    return {**recorder.status(), "recording": not request.app.state.recorders.items[camera_id][1].done()}


@router.post("/{camera_id}/stop")
async def stop_recording(camera_id: str, request: Request):
    _hls(camera_id)
    await request.app.state.recorders.stop(camera_id)
    recorder = request.app.state.recorders.get(camera_id)
    if recorder:
        return {**recorder.status(), "recording": False}
    return {"cameraId": camera_id, "availability": "not_recording", "recording": False, "bufferedSeconds": 0,
            "history": {"maxSecondsAgo": 0, "maxDurationS": MAX_EVIDENCE_SECONDS}}


class EpisodeRequest(BaseModel):
    cameraId: str = Field(pattern=r"^[A-Za-z0-9-]{1,100}$")
    secondsAgo: float = Field(gt=0)
    durationS: float = Field(gt=0, le=MAX_EVIDENCE_SECONDS)
    holdoutS: float = Field(default=0, ge=0)
    sceneCameraId: str | None = None
    bufferRevision: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    model_config = {"allow_inf_nan": False}


@router.post("")
async def create_episode(body: EpisodeRequest, request: Request):
    _hls(body.cameraId)
    recorder = request.app.state.recorders.get(body.cameraId)
    if not recorder:
        return JSONResponse({"error": "No history is recorded for this camera.", "availableSecondsAgo": 0,
                             "cameraId": body.cameraId}, status_code=409)
    try:
        manifest = await asyncio.to_thread(request.app.state.captures.create_hls_episode, recorder,
                                           body.secondsAgo, body.durationS, body.holdoutS, body.sceneCameraId, body.bufferRevision)
    except CaptureError as exc:
        return _error(exc)
    return {"manifest": manifest, "capture": request.app.state.captures.capture_contract(manifest["id"]).model_dump(mode="json")}


@router.post("/snapshots/{camera_id}")
async def capture_snapshot(camera_id: str, request: Request):
    """One DOT still. Snapshot episodes never support motion claims."""
    if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", camera_id) or camera_id in HLS_SOURCES:
        raise HTTPException(404, "Unknown snapshot camera.")
    from .main import camera_cache  # avoid a circular import at module load
    known = {c["id"]: c for c in (camera_cache.payload or [])}
    if camera_id not in known:
        raise HTTPException(404, "Snapshot camera is not in the current registry; refresh the camera directory.")
    coordinates = known.get(camera_id, {}).get("coordinates")
    url = DOT_IMAGE.format(id=camera_id)
    try:
        async with request.app.state.http.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
            response.raise_for_status()
            if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                raise CaptureError("Snapshot response was partial or encoded.")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_SNAPSHOT_BYTES:
                    raise CaptureError("The snapshot exceeded the size limit.")
            received_at = utcnow()
            if response.headers.get("content-length") is not None and int(response.headers["content-length"]) != len(data):
                raise CaptureError("Snapshot response was incomplete.")
            content_type = response.headers.get("content-type", "").split(";")[0].lower()
    except (CaptureError, ValueError):
        return JSONResponse({"error": "Snapshot bytes failed the size/framing audit."}, status_code=502)
    except httpx.HTTPError:
        return JSONResponse({"error": "The snapshot could not be retrieved.", "cameraId": camera_id}, status_code=502)
    try:
        manifest = await asyncio.to_thread(request.app.state.captures.create_snapshot_episode,
            camera_id, bytes(data), content_type, received_at, url, coordinates)
    except CaptureError as exc:
        return _error(exc)
    return {"manifest": manifest, "capture": request.app.state.captures.capture_contract(manifest["id"]).model_dump(mode="json")}


_MEDIA_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


@router.get("/{episode_id}/index.m3u8")
async def prefix_playlist(episode_id: str, request: Request):
    try:
        playlist = await asyncio.to_thread(request.app.state.captures.prefix_playlist, episode_id)
        return Response(playlist, media_type="application/vnd.apple.mpegurl", headers=_MEDIA_HEADERS)
    except CaptureError as exc:
        return _error(exc)


@router.get("/{episode_id}/segments/{index}")
async def prefix_segment(episode_id: str, index: int, request: Request):
    try:
        data = await asyncio.to_thread(request.app.state.captures.prefix_segment, episode_id, index)
        return Response(data, media_type="video/mp2t", headers=_MEDIA_HEADERS)
    except CaptureError as exc:
        return _error(exc)


@router.get("/{episode_id}/media")
async def prefix_media(episode_id: str, request: Request):
    try:
        data, mime = await asyncio.to_thread(request.app.state.captures.prefix_bytes, episode_id)
        return Response(data, media_type=mime, headers=_MEDIA_HEADERS)
    except CaptureError as exc:
        return _error(exc)


@router.get("")
async def list_episodes(request: Request):
    return {"episodes": request.app.state.captures.list()}


@router.get("/{episode_id}")
async def get_episode(episode_id: str, request: Request):
    try:
        return request.app.state.captures.get(episode_id)
    except CaptureError as exc:
        return _error(exc)


class PinRequest(BaseModel):
    jobId: str


@router.post("/{episode_id}/pins")
async def pin_episode(episode_id: str, body: PinRequest, request: Request):
    try:
        return {"episodeId": episode_id, "pins": request.app.state.captures.pin(episode_id, body.jobId)}
    except CaptureError as exc:
        return _error(exc)


@router.delete("/{episode_id}/pins/{job_id}")
async def unpin_episode(episode_id: str, job_id: str, request: Request):
    try:
        return {"episodeId": episode_id, "pins": request.app.state.captures.unpin(episode_id, job_id)}
    except CaptureError as exc:
        return _error(exc)
