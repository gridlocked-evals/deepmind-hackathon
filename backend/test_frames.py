"""Actual-pixel source-frame selection and retention regressions; no provider calls."""
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import json
import shutil
import subprocess

from PIL import Image
import pytest

from backend.artifacts import ArtifactError, ArtifactStore
from backend.capture import CaptureError, CaptureStore, HlsRecorder, sha256
from backend.frames import source_frame
from backend.test_capture import FakeCamera, FakeClock, CAMERA


def snapshot(tmp_path):
    captures, store = CaptureStore(tmp_path / "captures"), ArtifactStore(tmp_path / "artifacts")
    image = BytesIO()
    Image.new("RGB", (64, 48), (90, 30, 10)).save(image, format="PNG")
    manifest = captures.create_snapshot_episode("dot-frame-test", image.getvalue(), "image/png",
        datetime.now(timezone.utc), "https://example.invalid/snapshot")
    return captures, store, manifest


@pytest.mark.parametrize("at_s", [-1, float("nan"), float("inf"), float("-inf"), True])
def test_invalid_frame_time_rejects_before_capture_or_cache(tmp_path, at_s):
    captures, store, manifest = snapshot(tmp_path)
    source_frame(store, captures, manifest["id"], 0)
    with pytest.raises(ArtifactError) as error:
        source_frame(store, captures, manifest["id"], at_s)
    assert error.value.status == 422
    assert captures.pins(manifest["id"]) == []


def test_snapshot_time_validation_precedes_old_millisecond_cache(tmp_path):
    captures, store, manifest = snapshot(tmp_path)
    first = source_frame(store, captures, manifest["id"])
    assert first["frameIndex"] == 0 and first["mediaTimeS"] == 0
    assert first["sourceId"] == manifest["cameraId"]
    with pytest.raises(ArtifactError, match="single frame"):
        source_frame(store, captures, manifest["id"], .0001)
    assert captures.pins(manifest["id"]) == []


@pytest.mark.parametrize("field,value", [("sourceId", "wrong-source"), ("captureMediaSha256", "sha256:" + "f" * 64),
                                          ("mediaTimeS", .1)])
def test_cached_frame_provenance_cannot_be_reused_for_another_source(tmp_path, field, value):
    captures, store, manifest = snapshot(tmp_path)
    frame = source_frame(store, captures, manifest["id"])
    path = store.root / "frames" / (frame["id"] + ".json")
    path.chmod(0o644)
    frame[field] = value
    path.write_text(json.dumps(frame))
    with pytest.raises(ArtifactError) as error:
        source_frame(store, captures, manifest["id"])
    assert error.value.status == 409
    assert captures.pins(manifest["id"]) == []


def test_cached_frame_still_checks_original_capture_bytes(tmp_path):
    captures, store, manifest = snapshot(tmp_path)
    source_frame(store, captures, manifest["id"])
    path = captures.root / manifest["id"] / manifest["files"]["prefix"]["path"]
    path.chmod(0o644)
    path.write_bytes(b"changed original")
    with pytest.raises(CaptureError) as error:
        source_frame(store, captures, manifest["id"])
    assert error.value.status == 409
    assert captures.pins(manifest["id"]) == []


def test_source_is_pinned_during_index_and_released_on_failure(tmp_path, monkeypatch):
    import backend.frames as frames
    captures, store, manifest = snapshot(tmp_path)
    episode = manifest["id"]
    def failing_index(path, data):
        assert any(pin.startswith("frame-read-") for pin in captures.pins(episode))
        restarted = CaptureStore(captures.root, budget_bytes=1)
        assert restarted.enforce_budget() == []
        assert path.is_file()
        raise ArtifactError("scripted decode failure", status=422)
    monkeypatch.setattr(frames, "index_prefix", failing_index)
    with pytest.raises(ArtifactError, match="scripted decode"):
        source_frame(store, captures, episode)
    assert captures.pins(episode) == []


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")
def test_video_uses_actual_frame_pts_and_rejects_prefix_end(tmp_path):
    import asyncio
    segments = []
    for index in range(2):
        path = tmp_path / f"source-{index}.ts"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=15:duration=2",
            "-c:v", "libx264", "-g", "15", "-output_ts_offset", str(17 + index * 2), "-f", "mpegts", str(path)], check=True)
        segments.append(path.read_bytes())
    clock = FakeClock()
    camera = FakeCamera(clock)
    camera.body = lambda seq: segments[seq]
    recorder = HlsRecorder(CAMERA, camera, clock=clock)
    async def record():
        await recorder.poll_once()
        camera.advance()
        await recorder.poll_once()
    asyncio.run(record())
    captures, store = CaptureStore(tmp_path / "captures"), ArtifactStore(tmp_path / "artifacts")
    manifest = captures.create_hls_episode(recorder, 4, 4, holdout_s=2)
    # First original stream PTS is nonzero; reported media times are prefix-relative.
    frame = source_frame(store, captures, manifest["id"], .105)
    assert frame["frameIndex"] == 1
    assert frame["mediaTimeS"] == pytest.approx(1 / 15, abs=1e-6)
    assert frame["mediaTimeS"] != .105
    assert source_frame(store, captures, manifest["id"], .11) == frame
    holdout = captures.root / manifest["id"] / manifest["files"]["holdout"]["path"]
    holdout.chmod(0o644)
    holdout.write_bytes(b"heldout corruption is never read for a source frame")
    assert source_frame(store, captures, manifest["id"], 1.99)["frameIndex"] == 29
    with pytest.raises(ArtifactError, match="half-open"):
        source_frame(store, captures, manifest["id"], 2)
    assert captures.pins(manifest["id"]) == []
