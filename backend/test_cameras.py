import asyncio
import json
import time

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.feeds import HLS_SOURCES
from backend.main import CameraCache, app, normalize_dot
from backend.proxy import MAX_MEDIA, open_upstream, range_header, resolve_token, resource_token, rewrite_playlist, validate_url

CAMERA = "511ny-32998"
BASE = HLS_SOURCES[CAMERA]["_upstream"]


def test_public_api_starts_without_model_credentials(monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for name in ("GPU_BEARER_TOKEN", "COSMOS3_REASON_URL", "YOLO_URL", "WANDB_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        result = client.get("/api/vision/cameras").json()
        assert len(result["cameras"]) == 5
        vestry = next(camera for camera in result["cameras"] if camera["id"] == CAMERA)
        assert vestry["coordinates"] == [-74.01148453531307, 40.722891497915406]
        assert vestry["capturedAt"] is None
        assert vestry["kind"] == "hls"
        assert vestry["playbackUrl"] == f"/api/feeds/{CAMERA}/index.m3u8"
        assert "_upstream" not in vestry
        assert client.get("/api/feeds/not-allowed/index.m3u8").status_code == 404
        assert client.get(f"/api/feeds/{CAMERA}/resource/missing").status_code == 404


def test_playlist_rewrites_variants_segments_keys_and_maps():
    body = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",URI="audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=400000
chunklist.m3u8?session=one
#EXT-X-KEY:METHOD=AES-128,URI="key.bin?token=abc"
#EXT-X-MAP:URI="init.mp4"
#EXTINF:5,
media_123.ts
"""
    rewritten = rewrite_playlist(CAMERA, BASE, body)
    assert "skyvdn.com" not in rewritten
    import re
    tokens = re.findall(r"/resource/([A-Za-z0-9_-]+)", rewritten)
    assert len(tokens) == 5
    urls = [resolve_token(CAMERA, token) for token in tokens]
    assert any(url.endswith("chunklist.m3u8?session=one") for url in urls)
    assert any(url.endswith("key.bin?token=abc") for url in urls)
    assert any(url.endswith("media_123.ts") for url in urls)
    with pytest.raises(HTTPException):
        resolve_token("511ny-32991", tokens[0])


@pytest.mark.parametrize("bad", [
    "http://s9.nysdot.skyvdn.com/rtplive/R11_277/x.ts",
    "https://attacker.example/rtplive/R11_277/x.ts",
    "https://s9.nysdot.skyvdn.com.evil.example/rtplive/R11_277/x.ts",
    "https://user@s9.nysdot.skyvdn.com/rtplive/R11_277/x.ts",
    "https://s9.nysdot.skyvdn.com:8080/rtplive/R11_277/x.ts",
    "https://s9.nysdot.skyvdn.com/rtplive/R11_271/x.ts",
    "https://s9.nysdot.skyvdn.com/rtplive/R11_277/../secrets",
    "https://s9.nysdot.skyvdn.com/rtplive/R11_277/%252e%252e/secrets",
    "https://s9.nysdot.skyvdn.com/rtplive/R11_277/%5c..%5csecrets",
    "file:///etc/passwd",
])
def test_resource_locations_are_strictly_allowlisted(bad):
    with pytest.raises(HTTPException):
        validate_url(CAMERA, bad)


def test_playlist_rejects_unexpected_locations_and_bodies():
    for body in ("<html>offline</html>", "#EXTM3U\nhttps://evil.example/segment.ts", '#EXTM3U\n#EXT-X-KEY:URI="data:text/plain,secret"'):
        with pytest.raises(HTTPException):
            rewrite_playlist(CAMERA, BASE, body)


def test_redirect_escape_is_rejected_before_request():
    requested = []
    def handle(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(HTTPException):
                await open_upstream(client, CAMERA, BASE, {})
    asyncio.run(run())
    assert requested == [BASE]


def test_valid_redirect_and_range_transport():
    def handle(request):
        if request.url.path.endswith("playlist.m3u8"):
            return httpx.Response(302, headers={"Location": "chunklist.m3u8"})
        assert request.headers["Range"] == "bytes=0-15"
        return httpx.Response(206, content=b"0123456789abcdef", headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-15/40"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            response, url = await open_upstream(client, CAMERA, BASE, range_header("bytes=0-15"))
            assert url.endswith("chunklist.m3u8")
            assert await response.aread() == b"0123456789abcdef"
            await response.aclose()
    asyncio.run(run())
    for bad in ("bytes=0-10,20-30", "bytes=5-1", f"bytes=0-{MAX_MEDIA}", "bytes=abc-def"):
        with pytest.raises(HTTPException):
            range_header(bad)


def test_proxy_routes_rewrite_and_serve_media():
    def handle(request):
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(200, text="#EXTM3U\n#EXTINF:5,\nsegment.ts\n", headers={"Content-Type": "application/vnd.apple.mpegurl"})
        return httpx.Response(200, content=b"mpeg-ts-fixture", headers={"Content-Type": "video/mp2t"})
    with TestClient(app) as client:
        original = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        response = client.get(f"/api/feeds/{CAMERA}/index.m3u8")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        resource = response.text.splitlines()[-1]
        assert resource.startswith(f"/api/feeds/{CAMERA}/resource/")
        media = client.get(resource)
        assert media.status_code == 200
        assert media.content == b"mpeg-ts-fixture"
        assert media.headers["content-type"].startswith("video/mp2t")
        asyncio.run(app.state.http.aclose())
        app.state.http = original


def test_dot_normalization_filters_and_distinguishes_images():
    rows = [
        {"id": "dot-1", "name": "Example crossing", "area": "MANHATTAN", "latitude": 40.72, "longitude": -74.01, "isOnline": True, "imageUrl": "https://evil.example/image.jpg"},
        {"id": "outside", "area": "MANHATTAN", "latitude": 5, "longitude": 5},
        {"id": "wrong-area", "area": "WESTCHESTER", "latitude": 40.72, "longitude": -74.01},
        {"id": "../unsafe", "area": "MANHATTAN", "latitude": 40.72, "longitude": -74.01},
    ]
    normalized = normalize_dot(rows)
    assert len(normalized) == 1
    assert normalized[0]["kind"] == "snapshot"
    assert normalized[0]["capturedAt"] is None
    assert normalized[0]["playbackUrl"] == "https://webcams.nyctmc.org/api/cameras/dot-1/image"
    assert normalized[0]["availability"] == "unverified"
    assert normalized[0]["borough"] == "Manhattan"


def test_dot_includes_all_five_boroughs_and_offline_cameras_without_duplicates():
    locations = [
        ("Manhattan", 40.771797, -73.979217),
        ("Queens", 40.673832, -73.734442),
        ("Brooklyn", 40.60632, -73.989087),
        ("Bronx", 40.864552, -73.813781),
        ("Staten Island", 40.529434, -74.161351),
    ]
    rows = [{"id": f"dot-{index}", "area": borough, "latitude": lat,
             "longitude": lon, "isOnline": index != 4}
            for index, (borough, lat, lon) in enumerate(locations)]
    rows.extend([rows[0], {**rows[1], "id": "invalid", "latitude": "NaN"}])
    normalized = normalize_dot(rows)
    assert len(normalized) == 5
    assert {camera["borough"] for camera in normalized} == {row[0] for row in locations}
    assert normalized[-1]["availability"] == "unavailable"
    assert normalized[-1]["coordinates"] == [-74.161351, 40.529434]


def test_dot_citywide_metadata(monkeypatch):
    async def directory(_loader):
        return [{"id": "q", "borough": "Queens"}, {"id": "b", "borough": "Bronx"}], {"mode": "connected"}
    monkeypatch.setattr("backend.main.camera_cache.get", directory)
    with TestClient(app) as client:
        response = client.get("/api/cameras").json()
    assert response["metadata"]["coverage"] == "NYC five boroughs"
    assert response["metadata"]["total"] == 2
    assert response["metadata"]["boroughCounts"] == {
        "Manhattan": 0, "Brooklyn": 0, "Queens": 1, "Bronx": 1, "Staten Island": 0,
    }


def test_stale_cache_keeps_original_success_time():
    async def run():
        cache = CameraCache()
        async def good():
            return [{"id": "dot", "availability": "unverified", "capturedAt": None}]
        async def bad():
            raise httpx.ConnectError("private upstream detail")
        _, first = await cache.get(good)
        cache.loaded_at = time.monotonic() - 3601
        rows, stale = await cache.get(bad)
        assert stale["mode"] == "stale"
        assert stale["fetchedAt"] == first["fetchedAt"]
        assert rows[0]["fetchedAt"] == first["fetchedAt"]
        assert rows[0]["availability"] == "stale"
        assert "private upstream" not in json.dumps(stale)
        empty, unavailable = await CameraCache().get(bad)
        assert empty == [] and unavailable["mode"] == "unavailable"
    asyncio.run(run())


def test_expired_resource_token_is_not_fetchable(monkeypatch):
    token = resource_token(CAMERA, BASE)
    monkeypatch.setattr("backend.proxy.time.monotonic", lambda: time.time() + 10000)
    with pytest.raises(HTTPException):
        resolve_token(CAMERA, token)
