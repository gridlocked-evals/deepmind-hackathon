import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import threading

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


def fake_probe(path):
    return {"frames": 1 if path.suffix != ".ts" else 30, "width": 640, "height": 352,
            "duration": 0.0 if path.suffix != ".ts" else 2.0}


@pytest.fixture
def store(tmp_path):
    return CaptureStore(root=tmp_path / "captures", clock=FakeClock(),
                        probe=fake_probe)


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
    short = CaptureStore(root=store.root.parent / "short", probe=lambda p: {"frames": 30, "width": 1, "height": 1, "duration": 0.5})
    rec, _, _ = record(30)
    with pytest.raises(TruncatedCapture, match="Decoded media duration disagrees"):
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


def test_legacy_readonly_pins_can_be_updated_without_changing_evidence(store):
    recorder, _, _ = record(20)
    manifest = store.create_hls_episode(recorder, 10, 6)
    directory = store.root / manifest['id']
    (directory / 'pins.json').chmod(0o444)
    assert store.pin(manifest['id'], 'job-retained') == ['job-retained']
    assert store.get(manifest['id'], verify=True) == manifest
    assert (directory / 'manifest.json').stat().st_mode & 0o222 == 0


def test_pins_survive_cleanup_and_restart(tmp_path):
    root = tmp_path / "captures"
    probe = fake_probe
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


def test_pin_cannot_land_between_eviction_check_and_delete(tmp_path):
    root = tmp_path / "captures"
    probe = fake_probe
    clock = FakeClock()
    store = CaptureStore(root=root, budget_bytes=10**9, clock=clock, probe=probe)
    rec, _, _ = record(40)
    target = store.create_hls_episode(rec, seconds_ago=20, duration_s=10)["id"]
    store.budget_bytes = 1  # everything unpinned is now evictable
    checked, pin_done, pin_result = threading.Event(), threading.Event(), {}
    size = store._size

    def hooked_size(path):  # runs after the pins() check and before the delete
        if path.name == target and not checked.is_set():
            checked.set()
            pin_done.wait(1)  # without the lock the pin finishes here, inside the gap
        return size(path)

    store._size = hooked_size

    def pin():
        try:
            pin_result["pins"] = store.pin(target, "job-accepted-1")
        except CaptureError as error:
            pin_result["error"] = error
        pin_done.set()

    evict = threading.Thread(target=store.enforce_budget)
    evict.start()
    assert checked.wait(5)
    pinner = threading.Thread(target=pin)
    pinner.start()
    evict.join(5)
    pinner.join(5)
    # A pin that reports success must leave its episode on disk.
    if "pins" in pin_result:
        assert (root / target / "manifest.json").is_file(), "pinned episode was evicted"
    else:
        assert "Unknown capture episode" in str(pin_result["error"])


def test_pin_cannot_land_on_new_episode_before_its_fit_check(tmp_path, monkeypatch):
    import backend.capture as capture
    root = tmp_path / "captures"
    probe = fake_probe
    clock = FakeClock()
    store = CaptureStore(root=root, budget_bytes=10**9, clock=clock, probe=probe)
    rec, _, _ = record(40)
    old = store.create_hls_episode(rec, seconds_ago=20, duration_s=10)["id"]
    store.pin(old, "job-accepted-1")
    clock.now += timedelta(seconds=1)
    store.budget_bytes = store.usage_bytes() + 1  # the next episode cannot fit
    published, pin_done, pin_result = threading.Event(), threading.Event(), {}
    real_replace = os.replace

    def hooked_replace(src, dst):  # the gap right after the new episode becomes visible
        real_replace(src, dst)
        if Path(dst).parent == root and not published.is_set():
            published.set()
            pin_result["id"] = Path(dst).name
            pin_done.wait(1)  # without the lock the pin finishes here

    monkeypatch.setattr(capture.os, "replace", hooked_replace)

    def pin():
        try:
            pin_result["pins"] = store.pin(pin_result["id"], "job-accepted-2")
        except CaptureError as error:
            pin_result["error"] = error
        pin_done.set()

    create = threading.Thread(target=lambda: pytest.raises(CaptureError, store.create_hls_episode,
                                                            rec, seconds_ago=20, duration_s=10))
    create.start()
    assert published.wait(5)
    pinner = threading.Thread(target=pin)
    pinner.start()
    create.join(5)
    pinner.join(5)
    if "pins" in pin_result:  # a pin that reports success must leave its episode on disk
        assert (root / pin_result["id"] / "manifest.json").is_file(), "pinned new episode was deleted"
    else:
        assert "Unknown capture episode" in str(pin_result["error"])


def test_create_rejected_when_pinned_episodes_fill_budget(tmp_path):
    root = tmp_path / "captures"
    probe = fake_probe
    clock = FakeClock()
    store = CaptureStore(root=root, budget_bytes=10**9, clock=clock, probe=probe)
    rec, _, _ = record(40)
    pinned = store.create_hls_episode(rec, seconds_ago=20, duration_s=10)["id"]
    store.pin(pinned, "job-accepted-1")
    clock.now += timedelta(seconds=1)
    store.budget_bytes = store.usage_bytes() + 1
    with pytest.raises(CaptureError):
        store.create_hls_episode(rec, seconds_ago=20, duration_s=10)
    assert [m["id"] for m in store.list()] == [pinned]
    assert store.get(pinned, verify=True)["id"] == pinned


def test_rejected_create_does_not_evict_unpinned_episodes(tmp_path):
    root = tmp_path / "captures"
    probe = fake_probe
    clock = FakeClock()
    store = CaptureStore(root=root, budget_bytes=10**9, clock=clock, probe=probe)
    rec, _, _ = record(40)
    pinned = store.create_hls_episode(rec, seconds_ago=20, duration_s=10)["id"]
    store.pin(pinned, "job-accepted-1")
    clock.now += timedelta(seconds=1)
    unpinned = store.create_hls_episode(rec, seconds_ago=20, duration_s=10)["id"]
    clock.now += timedelta(seconds=1)
    # pinned + new can't fit even if `unpinned` were evicted, so nothing may be evicted
    store.budget_bytes = store.usage_bytes() - 1
    with pytest.raises(CaptureError):
        store.create_hls_episode(rec, seconds_ago=20, duration_s=10)
    assert sorted(m["id"] for m in store.list()) == sorted([pinned, unpinned])


def test_capture_rejected_without_successful_probe(tmp_path):
    store = CaptureStore(root=tmp_path / "c", probe=lambda p: None)
    rec, _, _ = record(20)
    with pytest.raises(TruncatedCapture, match="required decoded"):
        store.create_hls_episode(rec, seconds_ago=10, duration_s=6)
    assert not list(store.root.iterdir())


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")
def test_real_ts_segments_are_probed(tmp_path):
    segs = []
    for i in range(6):
        out = tmp_path / f"s{i}.ts"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size=352x240:rate=15:duration=2",
                        "-c:v", "libx264", "-g", "15", "-output_ts_offset", str(2 * i), "-f", "mpegts", str(out)], check=True)
        segs.append(out.read_bytes())
    clock = FakeClock()
    cam = FakeCamera(clock)
    cam.body = lambda seq: segs[seq % len(segs)]
    rec = HlsRecorder(CAMERA, cam, clock=clock)

    async def run():
        await rec.poll_once()
        for _ in range(5):
            cam.advance()
            await rec.poll_once()
    asyncio.run(run())
    store = CaptureStore(root=tmp_path / "captures")
    m = store.create_hls_episode(rec, seconds_ago=12, duration_s=12, holdout_s=2)
    assert (m["width"], m["height"]) == (352, 240)
    assert m["frameCount"] == 150 and m["effectiveFps"] == 15
    assert m["auditedPrefixDurationS"] == pytest.approx(10)
    assert m["auditedTotalDurationS"] == pytest.approx(12)
    assert m["segments"]["holdout"][0]["frameCount"] == 30
    # Actual held-out decode must fail even though the >8-second prefix is valid.
    rec.segments[-1].data = b"not decodable TS"
    rec.segments[-1].sha = sha256(rec.segments[-1].data)
    with pytest.raises(TruncatedCapture):
        store.create_hls_episode(rec, seconds_ago=12, duration_s=12, holdout_s=2)


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
        app.state.captures.probe = fake_probe
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
        assert client.get("/api/captures/cap_missing-1").status_code == 404
        del app.state.recorders.items[CAMERA]


def test_decoded_total_cap_includes_holdout(tmp_path):
    rec, _, _ = record(140)
    probe = lambda p: {**fake_probe(p), "duration": 2.1}
    store = CaptureStore(tmp_path / "captures", probe=probe)
    with pytest.raises(TruncatedCapture, match="120-second"):
        store.create_hls_episode(rec, seconds_ago=120, duration_s=120, holdout_s=20)
    assert not list(store.root.iterdir())


def test_holdout_must_decode_even_when_prefix_is_valid(tmp_path):
    rec, _, _ = record(20)
    store = CaptureStore(tmp_path / "captures", probe=lambda p: None if "holdout" in p.name else fake_probe(p))
    with pytest.raises(TruncatedCapture, match="required decoded"):
        store.create_hls_episode(rec, seconds_ago=10, duration_s=10, holdout_s=4)
    assert not list(store.root.iterdir())


def test_changed_buffer_revision_cannot_shift_selected_moment(store):
    rec, cam, _ = record(20)
    history = rec.status()["history"]
    assert history["segmentBoundariesS"] == list(range(0, 21, 2))
    cam.advance()
    asyncio.run(rec.poll_once())
    with pytest.raises(HistoryUnavailable, match="buffer changed"):
        store.create_hls_episode(rec, 10, 6, buffer_revision=history["bufferRevision"])
    current = rec.status()["history"]
    assert store.create_hls_episode(rec, 10, 6, buffer_revision=current["bufferRevision"])["totalDurationS"] == 6


def test_prefix_playlist_and_segments_never_read_holdout(store):
    rec, _, _ = record(20)
    manifest = store.create_hls_episode(rec, 10, 10, holdout_s=4)
    episode = manifest["id"]
    holdout = store.root / episode / manifest["files"]["holdout"]["path"]
    holdout.chmod(0o644)
    holdout.write_bytes(b"damaged suffix must never be read by prefix consumers")
    playlist = store.prefix_playlist(episode)
    assert playlist.count("#EXTINF:2.000000,") == 3
    assert "holdout" not in playlist and playlist.endswith("#EXT-X-ENDLIST\n")
    assert b"".join(store.prefix_segment(episode, index) for index in range(3)) == store.model_input(episode)[0].read_bytes()
    with pytest.raises(AccessDenied):
        store.prefix_segment(episode, 3)
    path = store.model_input(episode)[0]
    path.chmod(0o644)
    path.write_bytes(b"bad")
    with pytest.raises(CaptureError, match="no longer matches"):
        store.prefix_playlist(episode)


@pytest.mark.parametrize("damage", ["missing", "json", "shape"])
def test_missing_or_corrupt_pin_ledger_preserves_episode(store, damage):
    rec, _, _ = record(20)
    episode = store.create_hls_episode(rec, 10, 6)["id"]
    path = store.root / episode / "pins.json"
    path.chmod(0o644)
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("{" if damage == "json" else '{}')
    restarted = CaptureStore(store.root, budget_bytes=1, probe=fake_probe)
    assert restarted.enforce_budget() == []
    assert restarted.pins(episode) == ["capture-recovered-retention"]
    assert restarted.model_input(episode)[0].exists()


@pytest.mark.parametrize("damage", ["json", "metadata", "path", "symlink"])
def test_manifest_and_path_integrity_rejects_damage(store, damage):
    rec, _, _ = record(20)
    manifest = store.create_hls_episode(rec, 10, 6)
    path = store.root / manifest["id"] / "manifest.json"
    path.chmod(0o644)
    if damage == "json":
        path.write_text("{")
    elif damage == "symlink":
        media = store.root / manifest["id"] / manifest["files"]["prefix"]["path"]
        saved = media.with_name("source.ts")
        media.rename(saved)
        try:
            media.symlink_to(saved)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("This Windows account cannot create symbolic links")
            raise
    else:
        if damage == "metadata":
            manifest["cameraId"] = OTHER
        else:
            manifest["files"]["prefix"]["path"] = "../escape.ts"
            manifest["manifestSha256"] = store._manifest_hash(manifest)
        path.write_text(json.dumps(manifest))
    with pytest.raises(CaptureError) as error:
        store.model_input(manifest["id"])
    assert error.value.status == 409


def test_probe_output_and_process_lifetime_are_bounded():
    from backend.capture import _bounded_probe
    with pytest.raises(TruncatedCapture, match="output limit"):
        _bounded_probe([sys.executable, "-c", "import sys; sys.stdout.write('x'*100000)"])
    with pytest.raises(TruncatedCapture, match="time limit"):
        _bounded_probe([sys.executable, "-c", "import os,time; os.close(1); os.close(2); time.sleep(10)"], timeout=.1)


def test_probe_does_not_need_pipe_selectors_or_nonblocking_pipes(monkeypatch):
    # Windows cannot select() on pipes or set them non-blocking; the probe must not rely on either.
    import selectors
    from backend.capture import _bounded_probe

    def unsupported(*_args, **_kwargs):
        raise OSError("unsupported on Windows pipes")
    monkeypatch.setattr(selectors, "DefaultSelector", unsupported)
    monkeypatch.setattr(os, "set_blocking", unsupported)
    assert _bounded_probe([sys.executable, "-c", "import sys; sys.stdout.write('ok'*1000)"]) == b"ok" * 1000
    with pytest.raises(TruncatedCapture, match="decoded completely"):
        _bounded_probe([sys.executable, "-c", "import sys; sys.stderr.write('bad')"])


def test_real_decode_failure_and_bad_snapshot_are_rejected(tmp_path):
    store = CaptureStore(tmp_path / "captures")
    with pytest.raises(TruncatedCapture):
        store.create_snapshot_episode("dot-test", b"\xff\xd8\xffbroken", "image/jpeg", T0, "https://example.invalid")
    rec, _, _ = record(20)
    with pytest.raises(TruncatedCapture):
        store.create_hls_episode(rec, 10, 6)
    assert not list(store.root.iterdir())


def test_low_cadence_is_explicit(store):
    store.probe = lambda p: {**fake_probe(p), "frames": 4}
    rec, _, _ = record(20)
    manifest = store.create_hls_episode(rec, 10, 6)
    assert manifest["effectiveFps"] == 2 and "low_effective_cadence" in manifest["labels"]


def test_sequence_rollback_becomes_unavailable(store):
    rec, cam, _ = record(20)
    sealed = store.create_hls_episode(rec, 10, 6)
    cam.seq = 0
    for _ in range(3):
        asyncio.run(rec.poll_once())
    assert rec.availability() == "unavailable"
    assert "fresh recording" in rec.status()["lastError"]
    assert store.model_input(sealed["id"])[0].exists()


def test_retained_playback_http_shape_and_snapshot_rejection(store):
    from fastapi import FastAPI
    from backend.capture_api import router
    app = FastAPI()
    app.include_router(router)
    app.state.captures = store
    rec, _, _ = record(20)
    manifest = store.create_hls_episode(rec, 10, 10, holdout_s=4)
    episode = manifest["id"]
    with TestClient(app) as client:
        playlist = client.get(f"/api/captures/{episode}/index.m3u8")
        assert playlist.status_code == 200
        assert playlist.headers["content-type"] == "application/vnd.apple.mpegurl"
        assert playlist.headers["cache-control"] == "private, no-store"
        urls = [line for line in playlist.text.splitlines() if line.startswith("/api/")]
        assert len(urls) == 3
        assert b"".join(client.get(url).content for url in urls) == client.get(f"/api/captures/{episode}/media").content
        assert client.get(f"/api/captures/{episode}/segments/3").status_code == 403
        snapshot = store.create_snapshot_episode("dot-123", b"\xff\xd8\xffx", "image/jpeg", T0, "https://example.invalid")
        assert client.get(f"/api/captures/{snapshot['id']}/index.m3u8").status_code == 422


def test_capture_admission_is_bounded_and_released_after_failure(store):
    rec, _, _ = record(20)
    store._admission.acquire()
    store._admission.acquire()
    try:
        with pytest.raises(CaptureError) as error:
            store.create_hls_episode(rec, 10, 6)
        assert error.value.status == 503
    finally:
        store._admission.release()
        store._admission.release()
    store.probe = lambda p: None
    for _ in range(3):
        with pytest.raises(TruncatedCapture):
            store.create_hls_episode(rec, 10, 6)
    store.probe = fake_probe
    assert store.create_hls_episode(rec, 10, 6)["totalDurationS"] == 6


def test_stop_retains_buffer_and_restart_begins_fresh():
    from backend.capture_api import Recorders
    rec, _, _ = record(20)
    async def scenario():
        recorders = Recorders()
        task = asyncio.create_task(asyncio.sleep(60))
        recorders.items[CAMERA] = (rec, task)
        await recorders.stop(CAMERA)
        assert recorders.get(CAMERA) is rec and rec.buffered_seconds() == 20
        fresh = recorders.start(CAMERA, None)  # scheduled task is cancelled before any transport
        assert fresh is not rec and fresh.buffered_seconds() == 0
        await recorders.stop_all()
    asyncio.run(scenario())
