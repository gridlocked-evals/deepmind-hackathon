#!/usr/bin/env python3
"""Pre-capture demo footage from every camera and drop the slow ones.

Runs against a running backend (default http://localhost:8000), so it needs no
extra dependencies. For each 511NY video camera it records, checks that media
arrives at about real time, and saves and pins the latest clip. For each DOT
camera it saves one timed snapshot. If WORLD_API_TOKEN is set (or --token is
given), it also runs the paid Hydrogen extraction on each video clip.

The result goes to var/demo-cache.json, which the UI uses to offer
"Load cached clip" and to hide dropped cameras. Re-running replaces the cache
and unpins the previous run's captures so they can be evicted.

    python3 scripts/cache_demo_footage.py                  # footage only
    WORLD_API_TOKEN=... python3 scripts/cache_demo_footage.py   # + Hydrogen (paid)
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

PIN_JOB = "demo-cache"
OUT = Path(__file__).resolve().parent.parent / "var" / "demo-cache.json"


def call(base: str, path: str, body: dict | None = None, token: str = "", timeout: float = 60, method: str | None = None):
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = Request(base + path, data=data, headers=headers, method=method or ("POST" if data is not None else "GET"))
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read() or b"null")
    except HTTPError as exc:
        detail = exc.read()[:300].decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} on {path}: {detail}") from None


def latest_clip(status: dict, clip_s: float) -> tuple[float, float, str] | None:
    """Whole segments ending at the newest one, at most clip_s long (mirrors the UI's latestInterval)."""
    bounds = status["history"].get("segmentBoundariesS") or []
    if len(bounds) < 2:
        return None
    end = bounds[-1]
    start = next((b for b in bounds if b >= end - clip_s and b < end), bounds[-2])
    return end - start, end - start, status["history"]["bufferRevision"]


def unpin(base: str, entries) -> None:
    for entry in entries:
        try:
            call(base, f"/api/captures/{quote(entry['saved']['capture']['id'])}/pins/{PIN_JOB}", method="DELETE")
        except (RuntimeError, URLError, TimeoutError, ValueError):
            pass


class PinAttempts:
    """Remember pins inside workers, even when their result is never consumed."""
    def __init__(self):
        self._lock = Lock()
        self._entries = {}

    def remember(self, saved):
        with self._lock:
            self._entries[saved["capture"]["id"]] = {"saved": saved}

    def entries(self):
        with self._lock:
            return list(self._entries.values())


def cache_video(base: str, cameras: list[dict], args, kept: dict | None = None,
                pins: PinAttempts | None = None) -> tuple[dict, dict]:
    if kept is None:
        kept = {}
    dropped = {}
    started = time.monotonic()
    first_media: dict[str, tuple[float, float]] = {}  # camera -> (elapsed s, buffered s) when media first arrived
    statuses: dict[str, dict] = {}
    stopped_at: dict[str, float] = {}
    attempted = []
    stop_errors = {}
    try:
        for camera in cameras:
            # A timed-out start may still have succeeded on the server.
            attempted.append(camera["id"])
            call(base, f"/api/captures/{quote(camera['id'])}/recording", {})
        print(f"Recording {len(cameras)} video cameras for {args.record_seconds:.0f} s…", flush=True)
        while time.monotonic() - started < args.record_seconds:
            time.sleep(2)
            for camera in cameras:
                status = call(base, f"/api/captures/{quote(camera['id'])}/buffer")
                if status["bufferedSeconds"] > 0 and camera["id"] not in first_media:
                    first_media[camera["id"]] = (time.monotonic() - started, status["bufferedSeconds"])
    finally:
        # Stop every recorder before any slow decoding/sealing. The stop response
        # holds the final boundaries and revision; a last poll can already be stale.
        for cid in attempted:
            try:
                statuses[cid] = call(base, f"/api/captures/{quote(cid)}/stop", {})
                stopped_at[cid] = time.monotonic() - started
            except (RuntimeError, URLError, TimeoutError, ValueError) as exc:
                stop_errors[cid] = f"stop failed: {exc}"
                print(f"  warn: could not stop {cid}: {exc}", file=sys.stderr)
    for camera in cameras:
        cid, status = camera["id"], statuses.get(camera["id"], {})
        if cid in stop_errors:
            dropped[cid] = stop_errors[cid]
            continue
        # Slow = media took too long to start, or arrived well below real time once it did.
        start_s, start_buffered = first_media.get(cid, (None, 0))
        # Measured after first arrival: the first playlist fetch returns a backlog that would inflate the rate.
        rate = (status.get("bufferedSeconds", 0) - start_buffered) / max(1e-6, stopped_at[cid] - start_s) if start_s is not None else 0
        # Admission allows one segment of arrival slack; retain the measured rate in metadata.
        bounds = status.get("history", {}).get("segmentBoundariesS") or [0]
        slack = max((b - a for a, b in zip(bounds, bounds[1:])), default=0)
        admitted_rate = (status.get("bufferedSeconds", 0) - start_buffered + slack) / max(1e-6, stopped_at[cid] - start_s) if start_s is not None else 0
        reason = (status.get("lastError") or "no media received") if start_s is None \
            else f"first media after {start_s:.0f} s" if start_s > args.max_start_seconds \
            else f"media at {rate:.2f}x real time" if admitted_rate < args.min_realtime_ratio \
            else f"{status.get('repeatedSegments')} repeated segments" if status.get("repeatedSegments") else None
        clip = None if reason else latest_clip(status, args.clip_seconds)
        if reason or not clip:
            dropped[cid] = reason or "no complete segments"
            print(f"  drop {cid}: {dropped[cid]}")
            continue
        seconds_ago, duration, revision = clip
        try:
            saved = call(base, "/api/captures", {"cameraId": cid, "secondsAgo": seconds_ago, "durationS": duration,
                                                 "holdoutS": 0, "bufferRevision": revision})
            if pins is not None:
                pins.remember(saved)  # the server may pin successfully even if the response is lost
            call(base, f"/api/captures/{quote(saved['capture']['id'])}/pins", {"jobId": PIN_JOB})
        except (RuntimeError, URLError) as exc:
            dropped[cid] = f"save failed: {exc}"
            print(f"  drop {cid}: {dropped[cid]}")
            continue
        kept[cid] = {"kind": "hls", "saved": saved, "firstMediaS": round(start_s, 1), "realtimeRatio": round(rate, 2)}
        print(f"  keep {cid}: {duration:.1f} s clip, {rate:.2f}x real time")
    return kept, dropped


def cache_snapshot(base: str, camera: dict, max_seconds: float,
                   pins: PinAttempts | None = None) -> tuple[str, dict | str]:
    for attempt in range(4):
        t0 = time.monotonic()
        try:
            saved = call(base, f"/api/captures/snapshots/{quote(camera['id'])}", {}, timeout=max_seconds + 5)
            break
        except (RuntimeError, URLError, TimeoutError) as exc:
            # 503 means the backend's capture decoder is busy, not that the camera is slow.
            if "HTTP 503" not in str(exc) or attempt == 3:
                return camera["id"], f"snapshot failed: {exc}"
            time.sleep(0.5 * (attempt + 1))
    took = time.monotonic() - t0
    if took > max_seconds:
        return camera["id"], f"snapshot took {took:.1f} s"
    try:
        if pins is not None:
            pins.remember(saved)
        call(base, f"/api/captures/{quote(saved['capture']['id'])}/pins", {"jobId": PIN_JOB})
    except (RuntimeError, URLError) as exc:
        return camera["id"], f"pin failed: {exc}"
    return camera["id"], {"kind": "snapshot", "saved": saved, "fetchS": round(took, 2)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=os.environ.get("API_BASE", "http://localhost:8000"))
    parser.add_argument("--token", default=os.environ.get("WORLD_API_TOKEN", ""), help="enables paid Hydrogen extraction on video clips")
    parser.add_argument("--record-seconds", type=float, default=45)
    parser.add_argument("--clip-seconds", type=float, default=30, help="at most 120")
    parser.add_argument("--max-start-seconds", type=float, default=15, help="drop video whose first media takes longer")
    parser.add_argument("--min-realtime-ratio", type=float, default=0.8, help="drop video arriving slower than this")
    parser.add_argument("--snapshot-max-seconds", type=float, default=3, help="drop DOT cameras slower than this")
    parser.add_argument("--snapshot-workers", type=int, default=2, help="the backend decodes one capture at a time; more workers mostly hit 503 retries")
    parser.add_argument("--skip-snapshots", action="store_true")
    args = parser.parse_args()
    if not 0 < args.clip_seconds <= 120 or args.record_seconds < args.clip_seconds:
        parser.error("--clip-seconds must be in (0, 120] and no longer than --record-seconds")
    base = args.base.rstrip("/")

    call(base, "/api/health")
    # The previous run's pins stay until the replacement index is written: the old index still references them.
    previous = json.loads(OUT.read_text()).get("cameras", {}).values() if OUT.exists() else []
    kept: dict = {}
    pins = PinAttempts()
    try:
        video = call(base, "/api/vision/cameras")
        video = video.get("cameras", video) if isinstance(video, dict) else video
        kept, dropped = cache_video(base, video, args, kept, pins)

        if not args.skip_snapshots:
            dot = call(base, "/api/cameras", timeout=120)["cameras"]
            dot = [c for c in dot if c.get("kind") == "snapshot"]
            print(f"Saving {len(dot)} DOT snapshots (drop if slower than {args.snapshot_max_seconds:g} s)…", flush=True)
            with ThreadPoolExecutor(args.snapshot_workers) as pool:
                for index, (cid, result) in enumerate(pool.map(lambda c: cache_snapshot(base, c, args.snapshot_max_seconds, pins), dot), 1):
                    (kept if isinstance(result, dict) else dropped)[cid] = result
                    if index % 50 == 0:
                        print(f"  {index}/{len(dot)}", flush=True)

        if args.token:
            for cid, entry in kept.items():
                if entry["kind"] != "hls":
                    continue
                capture_id = entry["saved"]["capture"]["id"]
                print(f"Hydrogen extraction for {cid} (paid)…", flush=True)
                try:
                    report = call(base, "/api/observations/extract", {"cameraId": cid, "captureId": capture_id},
                                  token=args.token, timeout=600)
                    entry["extractionId"] = report["id"]
                except (RuntimeError, URLError, TimeoutError) as exc:
                    print(f"  extraction failed for {cid}: {exc}", file=sys.stderr)
        else:
            print("No WORLD_API_TOKEN: skipped Hydrogen extraction; the UI can still run it on a cached clip.")

        OUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schemaVersion": "demo-cache.v1", "createdAt": datetime.now(timezone.utc).isoformat(),
                                   "cameras": kept, "dropped": dropped}))
        tmp.replace(OUT)
    except BaseException:
        # The executor context has joined every worker before rollback. Some
        # successful/uncertain pins may never have reached the kept dictionary.
        unpin(base, pins.entries())
        raise
    retained = {entry["saved"]["capture"]["id"] for entry in kept.values()}
    unpin(base, [entry for entry in pins.entries() if entry["saved"]["capture"]["id"] not in retained])
    unpin(base, [entry for entry in previous if entry["saved"]["capture"]["id"] not in retained])
    print(f"\nKept {len(kept)} cameras, dropped {len(dropped)}. Wrote {OUT}")
    return 0 if kept else 1


if __name__ == "__main__":
    sys.exit(main())
