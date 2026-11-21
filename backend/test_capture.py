import asyncio
import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from backend.capture import CaptureService, MAX_EPISODE_SECONDS, audit_video, canonical, digest, parse_playlist, run_decoder
from backend.feeds import HLS_SOURCES

CAMERA = "511ny-32998"
BASE = HLS_SOURCES[CAMERA]["_upstream"]


async def fake_audit(data, duration):
    if data == b"truncated":
        raise HTTPException(502, "Truncated media")
    return {"durationSeconds": duration, "frameCount": int(duration * 10), "width": 640, "height": 360}


def playlist(sequence=1, count=3, duration=10):
    return "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:%d\n" % sequence + "".join(
        f"#EXTINF:{duration},\nsegment-{i}.ts\n" for i in range(sequence, sequence + count))


def client_for(body, media=None):
    def handler(request):
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(200, text=body)
        return httpx.Response(200, content=media or request.url.path.encode(), headers={"Content-Type": "video/mp2t"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_video_episode_seals_exact_bytes_and_isolates_suffix(tmp_path):
    async def run():
        async with client_for(playlist()) as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            assert service.history(CAMERA)["earliestSeconds"] is None
            history = await service.poll_once(CAMERA)
            assert history["earliestSeconds"] == 0
            assert history["latestSeconds"] == 30
            result = await service.create_episode(CAMERA, 0, 30, split=20, recording_id=history["recordingId"])
            assert result["durationSeconds"] == 30 and result["allowedInterval"]["endSeconds"] == 20
            assert result["sourceCapturedAt"] is None and result["clockUncertaintySeconds"] is None
            assert result["frameCount"] == 300
            context = service.capture_context(result["captureId"])
            assert context.receivedAt.endswith("Z")
            assert context.allowedInterval.endSeconds == 20
            assert "media" not in context.model_dump() and "frameCount" not in context.model_dump()
            original = b"".join((tmp_path / "episodes" / result["captureId"] / x["path"]).read_bytes() for x in result["media"])
            assert result["originalMediaSha256"] == hashlib.sha256(original).hexdigest()
            capture_id = result["captureId"]
            first, mime = service.read_prefix(capture_id, 0)
            assert first.endswith(b"segment-1.ts") and mime == "video/mp2t"
            with pytest.raises(HTTPException) as exc:
                service.read_prefix(capture_id, 2)
            assert exc.value.status_code == 403
            assert (tmp_path / "episodes" / capture_id / "prefix/0000.ts").exists()
            assert (tmp_path / "episodes" / capture_id / "suffix/0002.ts").exists()
            # Restarts load immutable evidence and durable pins, without re-fetching the camera.
            restored = CaptureService(tmp_path, client, auditor=fake_audit)
            assert restored.manifest(capture_id) == result
            assert restored.read_prefix(capture_id, 0)[0] == first
            assert restored.pins[capture_id] == ["capture-retention"]
            assert restored.history(CAMERA)["latestSeconds"] == 30
    asyncio.run(run())


def test_rejects_nonbuffered_interval_wrong_identity_and_duration(tmp_path):
    async def run():
        async with client_for(playlist(count=13)) as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            history = await service.poll_once(CAMERA)
            for start, end, options in [
                (0, MAX_EPISODE_SECONDS + 10, {}), (0, 0, {}), (0, 15, {}), (5, 20, {}),
                (0, 30, {"scene_id": "511ny-32991"}), (0, 30, {"recording_id": "rec_changed"}),
                (0, 30, {"split": 15}), (0, 30, {"split": 40}), (float("nan"), 30, {}),
            ]:
                with pytest.raises(HTTPException):
                    await service.create_episode(CAMERA, start, end, **options)
            assert history["latestSeconds"] == 130
            result = await service.create_episode(CAMERA, 10, 130)
            assert result["durationSeconds"] == 120
    asyncio.run(run())


def test_ring_eviction_never_changes_sealed_evidence(tmp_path):
    async def run():
        async with client_for(playlist(count=20)) as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            await service.poll_once(CAMERA)
            assert service.history(CAMERA)["earliestSeconds"] == 20
            result = await service.create_episode(CAMERA, 20, 40)
            original = service.read_prefix(result["captureId"], 0)[0]
            service.client = client_for(playlist(sequence=21, count=20))
            await service.poll_once(CAMERA)
            assert service.history(CAMERA)["earliestSeconds"] == 220
            assert service.read_prefix(result["captureId"], 0)[0] == original
            with pytest.raises(HTTPException):
                await service.create_episode(CAMERA, 20, 40)
            await service.client.aclose()
    asyncio.run(run())


def test_budget_keeps_accepted_and_job_pinned_captures(tmp_path):
    async def run():
        async with client_for(playlist()) as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            await service.poll_once(CAMERA)
            result = await service.create_episode(CAMERA, 0, 30)
            service.pin(result["captureId"], "job_1")
            service.unpin(result["captureId"], "capture-retention")
            service.max_bytes = service._usage()
            with pytest.raises(HTTPException) as exc:
                await service.create_episode(CAMERA, 0, 30)
            assert exc.value.status_code == 507
            assert service.manifest(result["captureId"]) == result
            assert CaptureService(tmp_path, client).pins[result["captureId"]] == ["job_1"]
    asyncio.run(run())


def test_corruption_and_truncation_fail_without_silent_acceptance(tmp_path):
    async def run():
        async with client_for(playlist(), media=b"truncated") as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            with pytest.raises(HTTPException):
                await service.poll_once(CAMERA)
            assert service.history(CAMERA)["latestSeconds"] is None
        async with client_for(playlist()) as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            await service.poll_once(CAMERA)
            result = await service.create_episode(CAMERA, 0, 30)
            media_path = tmp_path / "episodes" / result["captureId"] / "prefix/0000.ts"
            media_path.write_bytes(b"changed")
            with pytest.raises(HTTPException):
                service.read_prefix(result["captureId"], 0)
            manifest_path = media_path.parent.parent / "manifest.json"
            manifest_path.write_text(manifest_path.read_text().replace('"durationSeconds":30', '"durationSeconds":99'))
            with pytest.raises(HTTPException):
                service.manifest(result["captureId"])
    asyncio.run(run())


def test_source_gap_fails_instead_of_fabricating_continuity(tmp_path):
    async def run():
        async with client_for(playlist()) as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            await service.poll_once(CAMERA)
            changed = client_for(playlist(sequence=9))
            service.client = changed
            with pytest.raises(HTTPException) as exc:
                await service.poll_once(CAMERA)
            assert exc.value.status_code == 409
            assert service.history(CAMERA)["latestSeconds"] == 30
            await changed.aclose()
    asyncio.run(run())


def test_snapshot_cadence_unknown_clock_and_no_motion_claims(tmp_path):
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAACXBIWXMAAAABAAAAAQBPJcTWAAAAFElEQVR4nGNkIBGwjGoY1TB8NQAAYgAAPn161xsAAAAASUVORK5CYII=")
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=image, headers={"Content-Type": "image/png"})
    async def sources():
        return [{"id": "dot-test", "kind": "snapshot", "sourceUrl": "https://webcams.nyctmc.org/map", "coordinates": [-74.01, 40.72]}]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            service = CaptureService(tmp_path, client, snapshot_loader=sources)
            first = await service.capture_snapshot("dot-test")
            second = await service.capture_snapshot("dot-test")
            assert first["modality"] == "snapshot"
            assert first["durationSeconds"] == 0 and first["sourceCapturedAt"] is None
            assert first["motionClaimsAllowed"] is False
            assert not first["cadence"]["repeated"] and second["cadence"]["repeated"]
            assert second["cadence"]["uniqueFramesPerSecond"] is None
            assert service.capture_context(first["captureId"]).modality == "snapshot"
            assert first["originalMediaSha256"] == hashlib.sha256(image).hexdigest()
            assert service.read_prefix(first["captureId"], 0)[0] == image
            with pytest.raises(HTTPException):
                await service.capture_snapshot("unknown")
            with pytest.raises(HTTPException):
                await service.capture_snapshot("dot-test", scene_id="other")
    asyncio.run(run())
    assert calls == ["https://webcams.nyctmc.org/api/cameras/dot-test/image"] * 2


@pytest.mark.parametrize("extra", ["#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\"", "#EXT-X-MAP:URI=\"init.mp4\"", "#EXT-X-BYTERANGE:100", "#EXT-X-DISCONTINUITY", "#EXTINF:nan,"])
def test_unsupported_hls_fails_explicitly(extra):
    with pytest.raises(HTTPException):
        parse_playlist(CAMERA, BASE, "#EXTM3U\n" + extra + "\n#EXTINF:10,\na.ts\n")


def test_program_clock_is_preserved_without_claiming_exposure():
    parsed = parse_playlist(CAMERA, BASE, "#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:2026-11-21T12:00:00Z\n#EXTINF:10,\na.ts\n#EXTINF:10,\nb.ts\n")
    assert parsed["segments"][0]["sourceCapturedAt"] == "2026-11-21T12:00:00+00:00"
    assert parsed["segments"][1]["sourceCapturedAt"] == "2026-11-21T12:00:10+00:00"


def test_real_ffprobe_rejects_invalid_media():
    with pytest.raises(HTTPException):
        asyncio.run(audit_video(b"not-video", 5))


def test_restart_after_gap_creates_new_recording_and_keeps_episode(tmp_path):
    async def run():
        async with client_for(playlist()) as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            old = await service.poll_once(CAMERA)
            episode = await service.create_episode(CAMERA, 0, 30)
            ring = service._load_ring(CAMERA)
            ring.update(state="unavailable", error="gap")
            service._save_ring(ring)
            restarted = await service.start(CAMERA)
            assert restarted["recordingId"] != old["recordingId"]
            assert restarted["latestSeconds"] is None
            await service.close()
            assert service.manifest(episode["captureId"]) == episode
    asyncio.run(run())


def test_failed_reservation_does_not_leave_deleted_ring_references(tmp_path):
    async def run():
        async with client_for(playlist(count=18)) as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            await service.poll_once(CAMERA)
            service.max_bytes = 1
            changed = client_for(playlist(sequence=19, count=1))
            service.client = changed
            with pytest.raises(HTTPException):
                await service.poll_once(CAMERA)
            for segment in service._load_ring(CAMERA)["segments"]:
                assert (tmp_path / "rings" / CAMERA / (segment["sha256"] + ".ts")).exists()
            await changed.aclose()
    asyncio.run(run())


def test_real_ffprobe_accepts_complete_video_and_rejects_duration_lie(tmp_path):
    import shutil
    import subprocess
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe not installed")
    path = tmp_path / "fixture.ts"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=64x64:r=10", "-t", "2", "-c:v", "mpeg2video", "-f", "mpegts", str(path)], check=True)
    media = path.read_bytes()
    result = asyncio.run(audit_video(media, 2))
    assert result["frameCount"] == 20
    with pytest.raises(HTTPException):
        asyncio.run(audit_video(media, 30))


def test_decoded_duration_cannot_exceed_source_cap_despite_playlist_tolerance(tmp_path):
    async def slightly_longer(data, duration):
        return {"durationSeconds": duration * 125 / 120, "frameCount": 100, "width": 640, "height": 360}
    async def run():
        async with client_for(playlist(count=12)) as client:
            service = CaptureService(tmp_path, client, auditor=slightly_longer)
            await service.poll_once(CAMERA)
            with pytest.raises(HTTPException) as exc:
                await service.create_episode(CAMERA, 0, 120)
            assert exc.value.status_code == 422
            assert "Decoded original media duration" in exc.value.detail
            assert list((tmp_path / "episodes").iterdir()) == []
            accepted = await service.create_episode(CAMERA, 0, 110, split=50)
            assert accepted["durationSeconds"] == 110
            assert accepted["auditedDurationSeconds"] == pytest.approx(110 * 125 / 120)
            assert accepted["auditedPrefixDurationSeconds"] == pytest.approx(50 * 125 / 120)
            assert accepted["timelineBasis"] == "hls_declared_segment_duration"
            assert accepted["allowedInterval"]["endSeconds"] == 50
    asyncio.run(run())


def test_discontinuity_sequence_metadata_is_not_a_discontinuity_marker():
    parsed = parse_playlist(CAMERA, BASE, "#EXTM3U\n#EXT-X-DISCONTINUITY-SEQUENCE:0\n#EXTINF:5.005,\na.ts\n")
    assert parsed["segments"][0]["durationSeconds"] == 5.005
    with pytest.raises(HTTPException):
        parse_playlist(CAMERA, BASE, "#EXTM3U\n#EXT-X-DISCONTINUITY\n#EXTINF:5.005,\na.ts\n")


@pytest.mark.parametrize('damage', ['missing', 'malformed', 'wrong-shape'])
def test_pin_ledger_recovery_preserves_sealed_evidence(tmp_path, damage):
    async def run():
        async with client_for(playlist()) as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            await service.poll_once(CAMERA)
            episode = await service.create_episode(CAMERA, 0, 30)
            path = tmp_path / 'pins.json'
            if damage == 'missing':
                path.unlink()
            else:
                path.write_text('{' if damage == 'malformed' else '{"bad-owner": false}')
            restored = CaptureService(tmp_path, client, auditor=fake_audit)
            assert restored.pins[episode['captureId']] == ['capture-retention']
            restored.max_bytes = restored._usage()
            with pytest.raises(HTTPException) as failure:
                restored._reserve(1)
            assert failure.value.status_code == 507
            assert restored.manifest(episode['captureId']) == episode
            if damage != 'missing':
                assert list(tmp_path.glob('pins.corrupt-*.json'))
    asyncio.run(run())


def test_restart_removes_only_abandoned_preparation_pins(tmp_path):
    async def run():
        async with client_for(playlist()) as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            await service.poll_once(CAMERA)
            episode = await service.create_episode(CAMERA, 0, 30)
            service.pin(episode['captureId'], 'prepare_interrupted')
            service.pin(episode['captureId'], 'attempt_accepted')
            restored = CaptureService(tmp_path, client)
            assert restored.pins[episode['captureId']] == ['attempt_accepted', 'capture-retention']
    asyncio.run(run())


@pytest.mark.parametrize('damage', ['json', 'missing-hash', 'wrong-source', 'path-escape', 'partition-mismatch', 'time-gap'])
def test_manifest_corruption_returns_typed_integrity_failure(tmp_path, damage):
    async def run():
        async with client_for(playlist()) as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            await service.poll_once(CAMERA)
            episode = await service.create_episode(CAMERA, 0, 30)
            path = tmp_path / 'episodes' / episode['captureId'] / 'manifest.json'
            value = json.loads(path.read_text())
            value.pop('manifestSha256')
            if damage == 'wrong-source':
                value['captureId'] = 'cap_other'
            elif damage == 'path-escape':
                value['media'][0]['path'] = '../../private-data'
            elif damage == 'partition-mismatch':
                value['media'][0]['partition'] = 'suffix'
            elif damage == 'time-gap':
                value['media'][1]['startSeconds'] += 1
            if damage != 'missing-hash':
                value['manifestSha256'] = digest(canonical(value))
            path.write_text('{' if damage == 'json' else json.dumps(value))
            with pytest.raises(HTTPException) as failure:
                service.manifest(episode['captureId'])
            assert failure.value.status_code == 409
    asyncio.run(run())


def test_missing_media_is_a_typed_integrity_failure(tmp_path):
    async def run():
        async with client_for(playlist()) as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            await service.poll_once(CAMERA)
            episode = await service.create_episode(CAMERA, 0, 30)
            path = tmp_path / 'episodes' / episode['captureId'] / episode['media'][0]['path']
            path.unlink()
            with pytest.raises(HTTPException) as failure:
                service.read_prefix(episode['captureId'], 0)
            assert failure.value.status_code == 409
    asyncio.run(run())


def test_source_sequence_reset_requires_new_history_and_preserves_episode(tmp_path):
    async def run():
        async with client_for(playlist(sequence=10)) as client, client_for(playlist(sequence=1)) as reset:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            old = await service.poll_once(CAMERA)
            episode = await service.create_episode(CAMERA, 0, 30)
            service.client = reset
            await service.start(CAMERA)
            await service.tasks[CAMERA]
            assert service.history(CAMERA)['state'] == 'unavailable'
            new = await service.start(CAMERA)
            assert new['recordingId'] != old['recordingId']
            assert new['latestSeconds'] is None
            await service.close()
            assert service.manifest(episode['captureId']) == episode
    asyncio.run(run())


def test_corrupt_live_ring_can_restart_without_losing_sealed_captures(tmp_path):
    async def run():
        async with client_for(playlist()) as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            await service.poll_once(CAMERA)
            episode = await service.create_episode(CAMERA, 0, 30)
            (tmp_path / 'rings' / CAMERA / 'index.json').write_text('{')
            with pytest.raises(HTTPException) as failure:
                service.history(CAMERA)
            assert failure.value.status_code == 409
            restarted = await service.start(CAMERA)
            assert restarted['latestSeconds'] is None
            await service.close()
            assert service.manifest(episode['captureId']) == episode
    asyncio.run(run())


def test_decoder_timeout_includes_process_lifetime_after_pipes_close():
    import sys
    async def run():
        with pytest.raises(TimeoutError):
            await run_decoder([sys.executable, '-c', 'import os,time; os.close(1); os.close(2); time.sleep(3)'], output_limit=1024, timeout=.1)
    asyncio.run(run())


def test_decoder_output_is_bounded_before_collecting_the_whole_pipe():
    import sys
    async def run():
        with pytest.raises(HTTPException) as failure:
            await run_decoder([sys.executable, '-c', 'import sys; sys.stdout.write("x" * 1000000)'], output_limit=1024, timeout=3)
        assert failure.value.status_code == 413
    asyncio.run(run())


def test_sealed_vod_playlist_serves_only_verified_prefix_with_original_durations(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.capture import router
    async def capture():
        async with client_for(playlist(count=3, duration=5.005)) as client:
            service = CaptureService(tmp_path, client, auditor=fake_audit)
            await service.poll_once(CAMERA)
            episode = await service.create_episode(CAMERA, 0, 15.015, split=10.01)
            return service, episode
    service, episode = asyncio.run(capture())
    # The held-out bytes are neither opened nor advertised by the replay route.
    suffix = tmp_path / 'episodes' / episode['captureId'] / episode['media'][2]['path']
    suffix.write_bytes(b'not-for-review')
    app = FastAPI()
    app.state.capture = service
    app.include_router(router)
    with TestClient(app) as client:
        url = f"/api/captures/episodes/{episode['captureId']}/index.m3u8"
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers['content-type'] == 'application/vnd.apple.mpegurl'
        assert response.headers['cache-control'] == 'private, no-store'
        assert '#EXT-X-TARGETDURATION:6\n' in response.text
        assert '#EXT-X-PLAYLIST-TYPE:VOD\n' in response.text
        assert response.text.count('#EXTINF:5.005,') == 2
        assert response.text.endswith('#EXT-X-ENDLIST\n')
        media_urls = [line for line in response.text.splitlines() if line.startswith('/')]
        assert media_urls == [f"/api/captures/episodes/{episode['captureId']}/media/{index}" for index in (0, 1)]
        assert all(client.get(media_url).status_code == 200 for media_url in media_urls)
        assert client.get(f"/api/captures/episodes/{episode['captureId']}/media/2").status_code == 403
        prefix = tmp_path / 'episodes' / episode['captureId'] / episode['media'][0]['path']
        prefix.write_bytes(b'corrupted')
        assert client.get(url).status_code == 409


def test_vod_playlist_rejects_snapshot(tmp_path):
    from backend.test_evidence_media import snapshot_capture
    async def run():
        service, episode = await snapshot_capture(tmp_path)
        with pytest.raises(HTTPException) as failure:
            service.prefix_playlist(episode['captureId'])
        assert failure.value.status_code == 422
        await service.client.aclose()
    asyncio.run(run())


def test_capture_received_at_is_byte_receipt_not_decoding_completion(tmp_path, monkeypatch):
    import backend.capture as capture_module
    before = '2026-11-21T15:00:00+00:00'
    after = '2026-11-21T15:00:15+00:00'
    clock = [before]
    monkeypatch.setattr(capture_module, 'utc_now', lambda: clock[0])
    async def delayed_audit(data, duration):
        clock[0] = after
        return await fake_audit(data, duration)
    async def run():
        async with client_for(playlist(count=1)) as client:
            service = CaptureService(tmp_path, client, auditor=delayed_audit)
            await service.poll_once(CAMERA)
            episode = await service.create_episode(CAMERA, 0, 10)
            assert episode['receivedAt'] == before
            assert episode['createdAt'] == after
    asyncio.run(run())
