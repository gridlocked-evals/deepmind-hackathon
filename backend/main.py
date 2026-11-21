"""Public camera API for the Gotham Forge interface.

Run: python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
"""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .capture_api import Recorders, make_store, router as capture_router
from .contracts import json_schema
from .feeds import HLS_SOURCES, REGISTRY_URL, public_cameras, utc_now
from .proxy import proxy_media, resolve_token
from .gateway import GatewayConfig, ModelGateway

DOT_URL = "https://webcams.nyctmc.org/api/cameras"
DOT_PAGE = "https://webcams.nyctmc.org/map"
ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10, connect=5, pool=3), follow_redirects=False,
        limits=httpx.Limits(max_connections=12, max_keepalive_connections=8),
        headers={"User-Agent": "GothamForge/0.1 public-camera-viewer"},
    ) as client:
        app.state.http = client
        app.state.captures = make_store()
        app.state.recorders = Recorders()
        # Keep model traffic and camera transport on separate connection pools.
        config = GatewayConfig.from_environment()
        try:
            async with httpx.AsyncClient(follow_redirects=False,
                limits=httpx.Limits(max_connections=config.max_concurrency + 1)) as model_http:
                app.state.models = ModelGateway(config, model_http)
                yield
        finally:
            await app.state.recorders.stop_all()


app = FastAPI(title="Gotham Forge", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "DELETE"], allow_headers=["Range", "Content-Type"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)


class CameraCache:
    """Retain the successful fetch time when serving a stale directory."""
    def __init__(self):
        self.payload: list[dict] | None = None
        self.fetched_at: str | None = None
        self.loaded_at = 0.0
        self.lock = asyncio.Lock()

    async def get(self, loader):
        async with self.lock:
            if self.payload is not None and time.monotonic() - self.loaded_at < 3600:
                return self.payload, {"mode": "connected", "fetchedAt": self.fetched_at, "cached": True}
            try:
                payload = await loader()
                fetched = utc_now()
                self.payload = [{**camera, "fetchedAt": fetched} for camera in payload]
                self.fetched_at = fetched
                self.loaded_at = time.monotonic()
                return self.payload, {"mode": "connected", "fetchedAt": fetched, "cached": False}
            except (httpx.HTTPError, ValueError, TypeError):
                if self.payload is not None:
                    return [{**camera, "availability": "stale"} for camera in self.payload], {
                        "mode": "stale", "fetchedAt": self.fetched_at, "cached": True,
                        "error": "The camera directory could not be refreshed.",
                    }
                return [], {"mode": "unavailable", "fetchedAt": None, "cached": False,
                            "error": "The camera directory could not be reached."}


camera_cache = CameraCache()


def normalize_dot(rows: object) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError("Invalid camera directory")
    cameras = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("area", "")).upper() != "MANHATTAN":
            continue
        try:
            lat, lon = float(row["latitude"]), float(row["longitude"])
        except (ValueError, TypeError, KeyError):
            continue
        identifier = str(row.get("id", ""))
        if not (40.68 <= lat <= 40.89 and -74.04 <= lon <= -73.90 and re.fullmatch(r"[A-Za-z0-9-]{1,100}", identifier)):
            continue
        image_url = row.get("imageUrl")
        try:
            parsed = urlsplit(image_url) if isinstance(image_url, str) else None
            trusted = parsed and parsed.scheme == "https" and parsed.hostname == "webcams.nyctmc.org" and not parsed.username and parsed.port in (None, 443)
        except ValueError:
            trusted = False
        if not trusted:
            image_url = f"{DOT_URL}/{identifier}/image"
        cameras.append({
            "id": identifier, "name": str(row.get("name") or "NYC DOT camera"),
            "kind": "snapshot", "coordinates": [lon, lat], "provider": "NYC DOT",
            "sourceUrl": f"{DOT_PAGE}?cameras={identifier}",
            "playbackUrl": image_url, "thumbnailUrl": image_url,
            "description": "Periodic street image · source capture time unavailable",
            "capturedAt": None, "availability": "unverified" if str(row.get("isOnline", "")).lower() == "true" else "unavailable",
        })
    return cameras


async def fetch_dot(client: httpx.AsyncClient) -> list[dict]:
    async with client.stream("GET", DOT_URL) as response:
        response.raise_for_status()
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("Camera directory exceeds limit")
    return normalize_dot(json.loads(data))


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "gotham-forge-camera-api", "timestamp": utc_now()}


app.include_router(capture_router)


@app.get("/api/contracts/element119")
async def contracts():
    """JSON Schema for the frozen Element 119 contracts (docs/CONTRACTS.md)."""
    return json_schema()


@app.get("/api/cameras")
async def cameras(request: Request):
    items, metadata = await camera_cache.get(lambda: fetch_dot(request.app.state.http))
    return {"cameras": items, "metadata": {
        **metadata, "sourceUrl": DOT_PAGE, "directoryUrl": DOT_URL,
        "sourceKind": "snapshot", "captureClock": "unknown",
        "notice": "NYC DOT supplies periodic images. Retrieval time is not source capture time.",
    }}


@app.get("/api/models/status")
async def model_status(request: Request):
    """Read-only. Inspecting capability status never invokes a paid model."""
    return request.app.state.models.status()


@app.get("/api/vision/cameras")
async def live_cameras():
    return {"cameras": public_cameras(), "metadata": {
        "mode": "configured", "registryUrl": REGISTRY_URL, "captureClock": "unknown",
        "notice": "Configured public 511NY sources. Availability is verified by actual playback; stream delay is unknown.",
    }}


@app.get("/api/feeds/{camera_id}/index.m3u8")
async def feed_index(camera_id: str, request: Request):
    source = HLS_SOURCES.get(camera_id)
    if not source:
        raise HTTPException(404, "Unknown camera source.")
    return await proxy_media(request, camera_id, source["_upstream"])


@app.get("/api/feeds/{camera_id}/resource/{token}")
async def feed_resource(camera_id: str, token: str, request: Request):
    return await proxy_media(request, camera_id, resolve_token(camera_id, token))


# Optional production serving; development uses Vite and its /api proxy.
if (ROOT / "dist").is_dir():
    app.mount("/", StaticFiles(directory=str(ROOT / "dist"), html=True), name="frontend")
