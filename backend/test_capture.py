import asyncio
from datetime import datetime, timedelta, timezone
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.capture import (AccessDenied, CaptureError, CaptureStore, HistoryUnavailable, HlsRecorder,
                             SceneMismatch, TruncatedCapture, parse_playlist, sha256)
from backend.contracts import Capture
from backend.feeds import HLS_SOURCES

CAMERA = "511ny-32998"
OTHER = "511ny-32991"
MASTER = HLS_SOURCES[CAMERA]["_upstream"]
BASE = MASTER.rsplit("/", 1)[0]
T0 = datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class FakeCamera:
    """Live HLS source with a sliding three-segment window of 2-second segments."""

    def __init__(self, clock, program_time=False, repeat=False, segment=b"seg"):
        self.clock, self.seq, self.program_time, self.repeat, self.segment = clock, 0, program_time, repeat, segment
        self.fail = False

    def advance(self, n=1):
        self.seq += n
        self.clock.now += timedelta(seconds=2 * n)

    def body(self, seq):
        return self.segment if self.repeat else self.segment + b"-%d" % seq + b"." * 188

    async def __call__(self, url):
        if self.fail:
            raise CaptureError("upstream down")
        if url == MASTER:
            return b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nchunklist.m3u8\n", url
        if url.endswith("chunklist.m3u8"):
            first = max(0, self.seq - 2)
            lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:2", f"#EXT-X-MEDIA-SEQUENCE:{first}"]
            for s in range(first, self.seq + 1):
                if self.program_time:
                    lines.append(f"#EXT-X-PROGRAM-DATE-TIME:{(T0 + timedelta(seconds=2 * s - 7)).isoformat()}")  # 7 s broadcast delay
                lines += ["#EXTINF:2.0,", f"media_{s}.ts"]
            return "\n".join(lines).encode(), url
        seq = int(url.rsplit("_", 1)[1].split(".")[0])
        return self.body(seq), url


def record(seconds, **kw):
    clock = FakeClock()
    cam = FakeCamera(clock, **kw)
    rec = HlsRecorder(CAMERA, cam, clock=clock)

    async def run():
        await rec.poll_once()
        for _ in range(int(seconds / 2) - 1):
            cam.advance()
            await rec.poll_once()
    asyncio.run(run())
    return rec, cam, clock


@pytest.fixture
def store(tmp_path):
    return CaptureStore(root=tmp_path / "captures", clock=FakeClock(),
                        probe=lambda path: {"frames": 15 * 120, "width": 640, "height": 352, "duration": 120.0})


def test_parse_master_and_media_playlists():
    master = parse_playlist("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nchunk.m3u8\n", MASTER)
    assert master.variant_uri == f"{BASE}/chunk.m3u8"
    media = parse_playlist("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:7\n"
                           "#EXT-X-PROGRAM-DATE-TIME:2026-11-21T15:00:00Z\n#EXTINF:4.0,\na.ts\n#EXTINF:3.5,\nb.ts\n", MASTER)
    assert [(s.sequence, s.duration) for s in media.segments] == [(7, 4.0), (8, 3.5)]
    assert media.segments[1].program_time == T0 + timedelta(seconds=4)
    with pytest.raises(CaptureError, match="EXT-X-MAP"):
        parse_playlist('#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n', MASTER)


def test_fresh_camera_has_no_history_until_recorded(store):
    clock = FakeClock()
    rec = HlsRecorder(CAMERA, FakeCamera(clock), clock=clock)
    assert rec.status()["history"]["maxSecondsAgo"] == 0
    assert rec.status()["coordinates"] == HLS_SOURCES[CAMERA]["coordinates"]
    with pytest.raises(HistoryUnavailable) as err:
        rec.select(30, 10)
    assert err.value.detail["availableSecondsAgo"] == 0


def test_thirty_seconds_ago_only_inside_buffer(store):
    rec, _, _ = record(20)
    assert rec.status()["history"]["maxSecondsAgo"] == 20
    with pytest.raises(HistoryUnavailable):
        store.create_hls_episode(rec, seconds_ago=30, duration_s=10)
    rec, _, _ = record(40)
    manifest = store.create_hls_episode(rec, seconds_ago=30, duration_s=10)
    assert manifest["totalDurationS"] == 10
    assert [s["sequence"] for s in manifest["segments"]["prefix"]] == [5, 6, 7, 8, 9]


def test_cap_enforced_and_ring_bounded(store):
    rec, _, _ = record(260)
    assert rec.buffered_seconds() <= 180
    with pytest.raises(CaptureError, match="Duration must be within"):
        store.create_hls_episode(rec, seconds_ago=150, duration_s=121)
    manifest = store.create_hls_episode(rec, seconds_ago=150, duration_s=120, holdout_s=20)
    assert manifest["totalDurationS"] == 120
    assert manifest["files"]["prefix"]["durationS"] == 100
    assert manifest["files"]["holdout"]["durationS"] == 20


def test_clocks_are_distinguished(store):
    rec, _, _ = record(20, program_time=True)
    m = store.create_hls_episode(rec, seconds_ago=10, duration_s=6)
    assert m["clocks"]["sourceClock"] == "source_reported"
    assert m["segments"]["prefix"][0]["sourceTime"] is not None
    assert m["segments"]["prefix"][0]["receivedAt"] != m["segments"]["prefix"][0]["sourceTime"]
    rec, _, _ = record(20)
    m = store.create_hls_episode(rec, seconds_ago=10, duration_s=6)
    assert m["clocks"]["sourceClock"] == "unknown"
    assert "source_clock_unknown" in m["labels"]
    assert any(u["field"] == "exposureTime" for u in m["unknowns"])


def test_repeated_stills_stale_and_unavailable_are_labeled(store):
    rec, cam, clock = record(20, repeat=True)
    assert "repeated_stills" in store.create_hls_episode(rec, seconds_ago=10, duration_s=6)["labels"]
    rec, cam, clock = record(20)
    clock.now += timedelta(seconds=60)
    assert rec.availability() == "stale"
    assert "stale_source" in store.create_hls_episode(rec, seconds_ago=10, duration_s=6)["labels"]
    cam.fail = True
    for _ in range(3):
        asyncio.run(rec.poll_once())
    assert rec.availability() == "unavailable"
    with pytest.raises(HistoryUnavailable, match="unavailable"):
        store.create_hls_episode(rec, seconds_ago=10, duration_s=6)


def test_gaps_and_short_media_reject_truncation(store):
    rec, cam, _ = record(20)
    cam.advance(5)  # sequence jumps; segments in between were never received
    asyncio.run(rec.poll_once())
    with pytest.raises(TruncatedCapture, match="gap"):
        store.create_hls_episode(rec, seconds_ago=8, duration_s=8)
    short = CaptureStore(root=store.root.parent / "short", probe=lambda p: {"frames": 30, "width": 1, "height": 1, "duration": 2.0})
    rec, _, _ = record(30)
    with pytest.raises(TruncatedCapture, match="Decoded media is shorter"):
        short.create_hls_episode(rec, seconds_ago=20, duration_s=10)
    assert not any(short.root.iterdir())


def test_snapshot_episode_semantics(store):
    jpeg = b"\xff\xd8\xff" + b"0" * 100
    m = store.create_snapshot_episode("dot-123", jpeg, "image/jpeg", T0, "https://webcams.nyctmc.org/x", [-74.0, 40.7])
    assert m["sourceKind"] == "snapshot" and m["totalDurationS"] == 0
    assert {"snapshot_only", "no_motion_evidence"} <= set(m["labels"])
    capture = store.capture_contract(m["id"])
    assert capture.sourceKind == "snapshot" and capture.durationS == 0
    again = store.create_snapshot_episode("dot-123", jpeg, "image/jpeg", T0, "https://webcams.nyctmc.org/x")
    assert "repeated_still" in again["labels"]
    with pytest.raises(CaptureError, match="JPEG or PNG"):
        store.create_snapshot_episode("dot-123", b"<html>", "text/html", T0, "x")


def test_source_scene_mismatch(store):
    rec, _, _ = record(20)
    with pytest.raises(SceneMismatch):
        store.create_hls_episode(rec, seconds_ago=10, duration_s=6, scene_camera_id=OTHER)
    m = store.create_hls_episode(rec, seconds_ago=10, duration_s=6, scene_camera_id=CAMERA)
    with pytest.raises(SceneMismatch):
        store.model_input(m["id"], camera_id=OTHER)


def test_retries_reuse_immutable_bytes(store):
    rec, cam, _ = record(30)
    m = store.create_hls_episode(rec, seconds_ago=20, duration_s=10)
    first = store.model_input(m["id"])
    cam.advance(3)
    asyncio.run(rec.poll_once())  # the live camera moves on
    assert store.model_input(m["id"]) == first  # an inference retry sees the same moment
    assert store.capture_contract(m["id"]).mediaSha256 == first[1] == sha256(first[0].read_bytes())
    path = first[0]
    path.chmod(0o644)
    path.write_bytes(b"tampered")
    with pytest.raises(CaptureError, match="no longer matches"):
        store.model_input(m["id"])
    m2 = store.create_hls_episode(rec, seconds_ago=20, duration_s=10)
    assert m2["id"] != m["id"]


def test_holdout_is_isolated_from_model_inputs(store):
    rec, _, _ = record(40)
    m = store.create_hls_episode(rec, seconds_ago=30, duration_s=20, holdout_s=6)
    prefix, _ = store.model_input(m["id"])
    assert prefix.relative_to(store.root / m["id"]).parts[0] == "prefix"
    assert prefix.read_bytes() != (store.root / m["id"] / m["files"]["holdout"]["path"]).read_bytes()
    with pytest.raises(AccessDenied):
        store.holdout(m["id"], role="model")
    path, digest = store.holdout(m["id"], role="evaluation")
    assert sha256(path.read_bytes()) == digest
    assert store.capture_contract(m["id"]).durationS == m["files"]["prefix"]["durationS"]


def test_pins_survive_cleanup_and_restart(tmp_path):
    root = tmp_path / "captures"
    probe = lambda p: {"frames": 1800, "width": 640, "height": 352, "duration": 120.0}
    clock = FakeClock()
    store = CaptureStore(root=root, budget_bytes=10**9, clock=clock, probe=probe)
    rec, _, _ = record(40)
    episodes = []
    for _ in range(3):
        episodes.append(store.create_hls_episode(rec, seconds_ago=20, duration_s=10)["id"])
        clock.now += timedelta(seconds=1)
    store.pin(episodes[0], "job-accepted-1")
    restarted = CaptureStore(root=root, budget_bytes=1, clock=clock, probe=probe)
    assert restarted.pins(episodes[0]) == ["job-accepted-1"]
    removed = restarted.enforce_budget()
    assert set(removed) == {episodes[1], episodes[2]}
    assert restarted.get(episodes[0], verify=True)["id"] == episodes[0]
    assert (root / episodes[0] / "manifest.json").stat().st_mode & 0o222 == 0  # read-only


def test_capture_contract_valid_without_probe(tmp_path):
    store = CaptureStore(root=tmp_path / "c", probe=lambda p: None)
    rec, _, _ = record(20)
    m = store.create_hls_episode(rec, seconds_ago=10, duration_s=6)
    capture = store.capture_contract(m["id"])
    assert capture.frameCount is None and any(u.field == "frames" for u in capture.unknowns)
    Capture.model_validate(capture.model_dump(mode="json"))


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")
def test_real_ts_segments_are_probed(tmp_path):
    segs = []
    for i in range(3):
        out = tmp_path / f"s{i}.ts"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size=352x240:rate=15:duration=2",
                        "-c:v", "libx264", "-g", "15", "-output_ts_offset", str(2 * i), "-f", "mpegts", str(out)], check=True)
        segs.append(out.read_bytes())
    clock = FakeClock()
    cam = FakeCamera(clock)
    cam.body = lambda seq: segs[seq % 3]
    rec = HlsRecorder(CAMERA, cam, clock=clock)

    async def run():
        await rec.poll_once()
        cam.advance(); await rec.poll_once()
        cam.advance(); await rec.poll_once()
    asyncio.run(run())
    store = CaptureStore(root=tmp_path / "captures")
    m = store.create_hls_episode(rec, seconds_ago=6, duration_s=6)
    assert (m["width"], m["height"]) == (352, 240)
    assert 80 <= m["frameCount"] <= 95 and 14 <= m["effectiveFps"] <= 16


def test_api_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_DIR", str(tmp_path / "api"))
    from backend.main import app
    with TestClient(app) as client:
        assert client.get(f"/api/captures/{CAMERA}/buffer").json()["availability"] == "not_recording"
        refused = client.post("/api/captures", json={"cameraId": CAMERA, "secondsAgo": 30, "durationS": 10})
        assert refused.status_code == 409 and refused.json()["availableSecondsAgo"] == 0
        rec, _, _ = record(40)
        class Running:
            def done(self):
                return False
        app.state.recorders.items[CAMERA] = (rec, Running())  # injected recorder; no network
        ok = client.post("/api/captures", json={"cameraId": CAMERA, "secondsAgo": 30, "durationS": 12, "holdoutS": 4})
        assert ok.status_code == 200, ok.text
        body = ok.json()
        Capture.model_validate(body["capture"])
        episode = body["manifest"]["id"]
        assert client.get(f"/api/captures/{episode}").json()["files"]["holdout"]["durationS"] == 4
        assert client.post(f"/api/captures/{episode}/pins", json={"jobId": "job-1"}).json()["pins"] == ["job-1"]
        assert client.delete(f"/api/captures/{episode}/pins/job-1").json()["pins"] == []
        too_far = client.post("/api/captures", json={"cameraId": CAMERA, "secondsAgo": 90, "durationS": 10})
        assert too_far.status_code == 409
        assert client.post("/api/captures", json={"cameraId": CAMERA, "secondsAgo": 30, "durationS": 121}).status_code == 422
        assert client.get("/api/captures/cap_missing-1").status_code == 400
        del app.state.recorders.items[CAMERA]
