"""Public 511NY camera records adapted from Pharos revision 5bd6fd9.

Coordinates identify the actual source camera, never a nearby simulated scene.
The registry establishes configuration, not current playback availability.
"""
from datetime import datetime, timezone
import time

SOURCE_HOST = "s9.nysdot.skyvdn.com"
REGISTRY_URL = "https://api-511x-nysdot.carsprogram.org/cameras/map-features"
_ROWS = [
    ("32998", "Vestry Street", 40.722891497915406, -74.01148453531307, "R11_277", "640 × 352"),
    ("32991", "Houston Street", 40.72924236268597, -74.01051672537487, "R11_271", "352 × 240"),
    ("32997", "West 14th Street", 40.742276487161, -74.00871745903845, "R11_276", "352 × 240"),
    ("32996", "West 23rd Street", 40.74826078811978, -74.00762318468455, "R11_275", "352 × 240"),
    ("32995", "West 42nd Street", 40.762071202304085, -74.00091777520173, "R11_274", "352 × 240"),
]
HLS_SOURCES = {
    f"511ny-{identifier}": {
        "id": f"511ny-{identifier}", "providerCameraId": identifier,
        "name": f"West Street at {street}", "kind": "hls",
        "coordinates": [lon, lat], "provider": "511NY / NYSDOT",
        "sourceUrl": f"https://511ny.org/camera/{identifier}",
        "playbackUrl": f"/api/feeds/511ny-{identifier}/index.m3u8",
        "resolution": resolution, "capturedAt": None,
        "description": "Public roadway video · source clock and broadcast delay unknown",
        "_path": f"/rtplive/{stream}/",
        "_upstream": f"https://{SOURCE_HOST}/rtplive/{stream}/playlist.m3u8",
    }
    for identifier, street, lat, lon, stream, resolution in _ROWS
}
_events: dict[str, dict] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_fetch(camera_id: str, success: bool) -> None:
    previous = _events.get(camera_id, {})
    _events[camera_id] = {
        "success": success, "checkedAt": utc_now(), "epoch": time.monotonic(),
        "fetchedAt": utc_now() if success else previous.get("fetchedAt"),
    }


def public_cameras() -> list[dict]:
    result = []
    for camera_id, source in HLS_SOURCES.items():
        event = _events.get(camera_id)
        state = "unverified"
        if event:
            state = "connected" if event["success"] else "unavailable"
            if event["success"] and time.monotonic() - event["epoch"] > 60:
                state = "stale"
        result.append({
            **{key: value for key, value in source.items() if not key.startswith("_")},
            "availability": state, "fetchedAt": event.get("fetchedAt") if event else None,
        })
    return result
