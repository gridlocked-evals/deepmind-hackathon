import asyncio
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import httpx
import pytest
from fastapi import HTTPException

from backend.capture import CaptureService, audit_video
from backend.contracts import ImageRegion, TimeInterval
from backend.evidence_media import prepare_evidence

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg/ffprobe required")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAACXBIWXMAAAABAAAAAQBPJcTWAAAAFElEQVR4nGNkIBGwjGoY1TB8NQAAYgAAPn161xsAAAAASUVORK5CYII=")


def make_video(directory: Path, name="video", seconds=3, rate=5, offset=17):
    path = directory / f"{name}.ts"
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=64x48:rate={rate}",
                    "-t", str(seconds), "-c:v", "mpeg2video", "-output_ts_offset", str(offset), "-f", "mpegts", str(path)], check=True)
    return path.read_bytes()


def no_network(request):
    raise AssertionError("Evidence preparation must never recapture or fetch media")


async def video_capture(directory, prefix, *, suffix=None, duration=3, tamper_count=False):
    service = CaptureService(directory / "store", httpx.AsyncClient(transport=httpx.MockTransport(no_network)))
    audit = await audit_video(prefix, duration)
    media = [{"path": "prefix/0000.ts", "partition": "prefix", "startSeconds": 0, "endSeconds": duration,
              "byteLength": len(prefix), "sha256": hashlib.sha256(prefix).hexdigest(), "frameCount": audit["frameCount"] + int(tamper_count),
              "auditedDurationSeconds": audit["durationSeconds"]}]
    blobs = [(media[0]["path"], prefix)]
    if suffix is not None:
        media.append({"path": "suffix/0001.ts", "partition": "suffix", "startSeconds": duration, "endSeconds": duration * 2,
                      "byteLength": len(suffix), "sha256": hashlib.sha256(suffix).hexdigest(), "frameCount": 9999999,
                      "auditedDurationSeconds": duration})
        blobs.append((media[1]["path"], suffix))
    manifest = {"schemaVersion": "1.0", "captureId": "capture-test", "sourceId": "511ny-32998", "modality": "video",
                "durationSeconds": duration * (2 if suffix is not None else 1), "allowedInterval": {"startSeconds": 0, "endSeconds": duration},
                "receivedAt": "2026-11-21T15:00:00Z", "sourceCapturedAt": None, "sourceClock": "unknown", "clockUncertaintySeconds": None,
                "timelineBasis": "hls_declared_segment_duration", "media": media}
    return service, service._persist_episode(manifest, blobs)


async def snapshot_capture(directory):
    service = CaptureService(directory / "store", httpx.AsyncClient(transport=httpx.MockTransport(no_network)))
    manifest = {"schemaVersion": "1.0", "captureId": "snapshot-test", "sourceId": "dot-test", "modality": "snapshot",
                "durationSeconds": 0, "allowedInterval": {"startSeconds": 0, "endSeconds": 0},
                "receivedAt": "2026-11-21T15:00:00Z", "sourceCapturedAt": None, "sourceClock": "unknown", "clockUncertaintySeconds": None,
                "media": [{"path": "prefix/0000.image", "partition": "prefix", "startSeconds": 0, "endSeconds": 0,
                           "byteLength": len(PNG), "sha256": hashlib.sha256(PNG).hexdigest(), "frameCount": 1, "mimeType": "image/png"}]}
    return service, service._persist_episode(manifest, [("prefix/0000.image", PNG)])


def test_actual_pts_sampling_frame_indices_and_suffix_isolation(tmp_path):
    video = make_video(tmp_path)
    async def run():
        captures, manifest = await video_capture(tmp_path, video, suffix=b"not-decodable-held-out-secret")
        reads = []
        original = captures.read_prefix
        def tracked(capture_id, index):
            reads.append(index)
            return original(capture_id, index)
        captures.read_prefix = tracked
        prepared = await prepare_evidence(captures, manifest["captureId"])
        assert reads == [0]
        assert [frame.time_seconds for frame in prepared.frames] == [0.0, 1.0, 2.0, 2.8]
        assert [frame.frame_index for frame in prepared.frames] == [0, 5, 10, 14]
        assert all(frame.source_pts_seconds > 17 for frame in prepared.frames)
        assert all(frame.data.startswith(b"\xff\xd8") for frame in prepared.frames)
        assert prepared.sampling["decodedPrefixFrameCount"] == 15
        assert prepared.sampling["omittedEligibleFrameCount"] == 11
        assert "9999999" not in json.dumps(prepared.metadata())
        assert "held-out-secret" not in json.dumps(prepared.metadata())
        assert all(frame.to_evidence_ref().captureId == manifest["captureId"] for frame in prepared.frames)
        assert captures.pins[manifest["captureId"]] == ["capture-retention"]
        await captures.client.aclose()
    asyncio.run(run())


def test_bounded_sampling_covers_whole_window_instead_of_cutting_tail(tmp_path):
    video = make_video(tmp_path, seconds=6)
    async def run():
        captures, manifest = await video_capture(tmp_path, video, duration=6)
        prepared = await prepare_evidence(captures, manifest["captureId"], max_frames=3)
        assert len(prepared.frames) == 3
        assert prepared.frames[0].time_seconds == 0
        assert prepared.frames[-1].time_seconds == 5.8
        assert prepared.sampling["minimumSpacingSeconds"] == pytest.approx(2.9)
        assert prepared.sampling["maximumSampleGapSeconds"] == 3.0
        assert prepared.sampling["eligibleFrameCount"] == 30
        assert prepared.sampling["sampledFrameCount"] == 3
        await captures.client.aclose()
    asyncio.run(run())


def test_reinspection_uses_actual_pts_and_maps_crop_to_original_image(tmp_path):
    video = make_video(tmp_path)
    async def run():
        captures, manifest = await video_capture(tmp_path, video)
        crop = ImageRegion(x=0.25, y=0.25, width=0.5, height=0.5)
        prepared = await prepare_evidence(captures, manifest["captureId"],
            window=TimeInterval(startSeconds=0.3, endSeconds=1.7), crop=crop)
        assert prepared.capture.allowedInterval == TimeInterval(startSeconds=0.3, endSeconds=1.7)
        assert [frame.time_seconds for frame in prepared.frames] == [0.4, 1.4, 1.6]
        assert [frame.frame_index for frame in prepared.frames] == [2, 7, 8]
        frame = prepared.frames[0]
        assert (frame.width, frame.height) == (32, 24)
        mapped = frame.to_evidence_ref(ImageRegion(x=0.5, y=0.5, width=0.5, height=0.5))
        assert mapped.region == ImageRegion(x=0.5, y=0.5, width=0.25, height=0.25)
        assert all(0.3 <= frame.time_seconds < 1.7 for frame in prepared.frames)
        await captures.client.aclose()
    asyncio.run(run())


def test_snapshot_keeps_original_bytes_and_has_no_temporal_claims(tmp_path):
    async def run():
        captures, manifest = await snapshot_capture(tmp_path)
        prepared = await prepare_evidence(captures, manifest["captureId"], max_frames=1)
        assert len(prepared.frames) == 1 and prepared.frames[0].data == PNG
        assert prepared.frames[0].time_seconds == 0 and prepared.frames[0].frame_index == 0
        assert prepared.frames[0].source_pts_seconds is None
        assert prepared.sampling["supportsTemporalOrdering"] is False
        cropped = await prepare_evidence(captures, manifest["captureId"], crop=ImageRegion(x=0.1, y=0.1, width=0.4, height=0.4))
        assert cropped.frames[0].region == ImageRegion(x=1/16, y=1/16, width=7/16, height=7/16)
        assert (cropped.frames[0].width, cropped.frames[0].height) == (7, 7)
        with pytest.raises(HTTPException):
            await prepare_evidence(captures, manifest["captureId"], window=TimeInterval(startSeconds=0, endSeconds=1))
        await captures.client.aclose()
    asyncio.run(run())


def test_errors_release_pin_and_do_not_silently_truncate(tmp_path):
    video = make_video(tmp_path)
    async def run():
        captures, manifest = await video_capture(tmp_path, video, suffix=b"held-out")
        for arguments, expected in [
            ({"window": TimeInterval(startSeconds=2, endSeconds=4)}, 422),
            ({"window": TimeInterval(startSeconds=1, endSeconds=1)}, 422),
            ({"max_frames": 1}, 422), ({"max_payload_bytes": 1024}, 413),
        ]:
            with pytest.raises(HTTPException) as exc:
                await prepare_evidence(captures, manifest["captureId"], **arguments)
            assert exc.value.status_code == expected
            assert captures.pins[manifest["captureId"]] == ["capture-retention"]
        await captures.client.aclose()
    asyncio.run(run())


def test_immutable_frame_audit_mismatch_fails(tmp_path):
    video = make_video(tmp_path)
    async def run():
        captures, manifest = await video_capture(tmp_path, video, tamper_count=True)
        with pytest.raises(HTTPException) as exc:
            await prepare_evidence(captures, manifest["captureId"])
        assert exc.value.status_code == 502
        assert "frame count" in exc.value.detail
        await captures.client.aclose()
    asyncio.run(run())


def test_original_image_corruption_is_rejected_before_preparation(tmp_path):
    async def run():
        captures, manifest = await snapshot_capture(tmp_path)
        (captures.root / "episodes" / manifest["captureId"] / "prefix/0000.image").write_bytes(b"corrupted")
        with pytest.raises(HTTPException) as exc:
            await prepare_evidence(captures, manifest["captureId"])
        assert exc.value.status_code == 409
        assert captures.pins[manifest["captureId"]] == ["capture-retention"]
        await captures.client.aclose()
    asyncio.run(run())


def test_multiple_segments_keep_capture_offsets_and_original_frame_indices(tmp_path):
    first = make_video(tmp_path, name="first", seconds=2, offset=17)
    second = make_video(tmp_path, name="second", seconds=2, offset=80)
    async def run():
        captures, _ = await video_capture(tmp_path, first, duration=2)
        audit = await audit_video(second, 2)
        manifest = captures.manifest("capture-test")
        manifest.pop("manifestSha256")
        manifest.pop("originalMediaSha256")
        manifest["captureId"] = "multi-segment"
        manifest["durationSeconds"] = 4
        manifest["allowedInterval"]["endSeconds"] = 4
        manifest["media"].append({"path": "prefix/0001.ts", "partition": "prefix", "startSeconds": 2, "endSeconds": 4,
            "byteLength": len(second), "sha256": hashlib.sha256(second).hexdigest(), "frameCount": audit["frameCount"],
            "auditedDurationSeconds": audit["durationSeconds"]})
        captures._persist_episode(manifest, [("prefix/0000.ts", first), ("prefix/0001.ts", second)])
        prepared = await prepare_evidence(captures, "multi-segment")
        assert [frame.time_seconds for frame in prepared.frames] == [0, 1, 2, 3, 3.8]
        assert [frame.frame_index for frame in prepared.frames] == [0, 5, 10, 15, 19]
        assert prepared.frames[2].source_pts_seconds > 80
        assert prepared.frames[2].source_media_index == 1
        await captures.client.aclose()
    asyncio.run(run())


def test_cancellation_releases_active_preparation_pin(tmp_path, monkeypatch):
    import backend.evidence_media as media_module
    started = asyncio.Event()
    async def blocked(*args, **kwargs):
        started.set()
        await asyncio.Future()
    monkeypatch.setattr(media_module, "_probe", blocked)
    async def run():
        captures, manifest = await snapshot_capture(tmp_path)
        task = asyncio.create_task(prepare_evidence(captures, manifest["captureId"]))
        await started.wait()
        assert len(captures.pins[manifest["captureId"]]) == 2
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert captures.pins[manifest["captureId"]] == ["capture-retention"]
        await captures.client.aclose()
    asyncio.run(run())


def test_jpeg_splitter_honors_metadata_lengths_and_rejects_truncation():
    from backend.evidence_media import _jpeg_images
    # An EOI byte pattern inside APP metadata is not an image boundary.
    jpeg = b"\xff\xd8\xff\xe0\x00\x06ab\xff\xd9\xff\xda\x00\x02\x01\xff\x00\x02\xff\xd9"
    assert _jpeg_images(jpeg + jpeg) == [jpeg, jpeg]
    with pytest.raises(HTTPException):
        _jpeg_images(jpeg[:-1])


def test_oversized_decoded_dimensions_reject_before_image_extraction(tmp_path, monkeypatch):
    import backend.evidence_media as media_module
    async def oversized_probe(*args, **kwargs):
        return json.dumps({'streams': [{'width': 65536, 'height': 65536}],
                           'frames': [{'width': 65536, 'height': 65536}]}).encode()
    monkeypatch.setattr(media_module, '_run', oversized_probe)
    async def run():
        captures, manifest = await snapshot_capture(tmp_path)
        with pytest.raises(HTTPException) as failure:
            await prepare_evidence(captures, manifest['captureId'])
        assert failure.value.status_code == 502
        assert captures.pins[manifest['captureId']] == ['capture-retention']
        await captures.client.aclose()
    asyncio.run(run())


def test_decoder_commands_limit_protocols_and_single_allocations(tmp_path, monkeypatch):
    import backend.evidence_media as media_module
    original = media_module._run
    commands = []
    async def record(command, **kwargs):
        commands.append(command)
        return await original(command, **kwargs)
    monkeypatch.setattr(media_module, '_run', record)
    async def run():
        captures, manifest = await snapshot_capture(tmp_path)
        await prepare_evidence(captures, manifest['captureId'], crop=ImageRegion(x=0,y=0,width=.5,height=.5))
        assert len(commands) == 2
        assert all(command[command.index('-protocol_whitelist') + 1] == 'file,pipe' for command in commands)
        assert all(int(command[command.index('-max_alloc') + 1]) == 64 * 1024 * 1024 for command in commands)
        await captures.client.aclose()
    asyncio.run(run())
