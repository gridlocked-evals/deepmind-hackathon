"""Cache metadata and recorder lifecycle regressions; all transport is in-process or fake."""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from backend import main
from scripts import cache_demo_footage as cache


@pytest.mark.parametrize("legacy", [False, True])
def test_public_cache_references_protected_extraction_without_serving_it(tmp_path, monkeypatch, legacy):
    report = {"id": "extract_fixture", "observations": ["operator-only fixture"]}
    entry = {"kind": "hls", "saved": {"capture": {"id": "capture-fixture"}},
             **({"extraction": report} if legacy else {"extractionId": report["id"]})}
    payload = {"schemaVersion": "demo-cache.v1", "createdAt": "fixture-time",
               "cameras": {"video": entry, "still": {"kind": "snapshot", "saved": {}}}, "dropped": {}}
    (tmp_path / "var").mkdir()
    (tmp_path / "var/demo-cache.json").write_text(json.dumps(payload))
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setenv("WORLD_API_TOKEN", "fixture-operator-token-123456789012345")
    monkeypatch.setattr(main.app.state, "artifacts", SimpleNamespace(get=lambda kind, identifier: report), raising=False)
    client = TestClient(main.app)
    response = client.get("/api/demo-cache")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    public = response.json()["cameras"]
    assert public["video"] == {"kind": "hls", "saved": entry["saved"], "extractionId": report["id"]}
    assert "operator-only fixture" not in response.text and "extractionId" not in public["still"]
    url = "/api/observations/extractions/" + report["id"]
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer fixture-operator-token-123456789012345"}).json() == report


def test_missing_public_cache_keeps_live_workflow_available(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ROOT", tmp_path)
    assert TestClient(main.app).get("/api/demo-cache").status_code == 404


class FakeClock:
    now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeCaptureAPI:
    def __init__(self, failure=None):
        self.active = set()
        self.stopped = []
        self.saved = []
        self.polls = {}
        self.failure = failure

    @staticmethod
    def status(camera_id, final=False, buffered=10):
        buffered = 16 if final else buffered
        return {"bufferedSeconds": buffered, "repeatedSegments": 0,
                "history": {"segmentBoundariesS": list(range(0, buffered + 1, 2)),
                            "bufferRevision": f"{camera_id}-{'final' if final else 'polled'}"}}

    def __call__(self, base, path, body=None, **kwargs):
        camera_id = path.split("/")[3] if path.startswith("/api/captures/") else None
        if path.endswith("/recording"):
            self.active.add(camera_id)
            if self.failure == "startup" and camera_id == "camera-b":
                raise TimeoutError("start response lost after admission")
            if self.failure == "startup_fatal" and camera_id == "camera-b":
                raise ValueError("malformed startup response")
            return {}
        if path.endswith("/buffer"):
            if self.failure == "poll":
                raise RuntimeError("poll failed")
            if self.failure == "poll_fatal":
                return {}  # malformed status must still abort and clean up every attempted recorder
            self.polls[camera_id] = self.polls.get(camera_id, 0) + 1
            return self.status(camera_id, buffered=8 + 2 * self.polls[camera_id])
        if path.endswith("/stop"):
            self.stopped.append(camera_id)
            self.active.discard(camera_id)
            if self.failure == "stop" and camera_id == "camera-a":
                raise TimeoutError("stop response lost")
            return self.status(camera_id, final=True)
        if path == "/api/captures":
            assert not self.active, "All recorders must stop before the first slow seal"
            assert body["bufferRevision"] == f"{body['cameraId']}-final"
            self.saved.append(body)
            return {"capture": {"id": "capture-" + body["cameraId"]}}
        assert path.endswith("/pins")
        return {}


def cache_args():
    return SimpleNamespace(record_seconds=6, clip_seconds=6, max_start_seconds=15, min_realtime_ratio=.8)


@pytest.mark.parametrize("camera_ids", [("camera-a",), ("camera-a", "camera-b")])
def test_video_cache_stops_all_sources_and_seals_final_revisions(monkeypatch, camera_ids):
    api = FakeCaptureAPI()
    monkeypatch.setattr(cache, "call", api)
    monkeypatch.setattr(cache, "time", FakeClock())
    kept, dropped = cache.cache_video("fixture", [{"id": cid} for cid in camera_ids], cache_args())
    assert set(kept) == set(camera_ids) and not dropped
    assert api.stopped == list(camera_ids) and len(api.saved) == len(camera_ids)
    assert all(body["secondsAgo"] == body["durationS"] == 6 for body in api.saved)


@pytest.mark.parametrize("failure", ["startup_fatal", "poll_fatal"])
def test_video_cache_cleans_up_recorders_after_partial_failure(monkeypatch, failure):
    api = FakeCaptureAPI(failure)
    monkeypatch.setattr(cache, "call", api)
    monkeypatch.setattr(cache, "time", FakeClock())
    with pytest.raises((ValueError, KeyError)):
        cache.cache_video("fixture", [{"id": "camera-a"}, {"id": "camera-b"}], cache_args())
    assert not api.active and api.stopped == ["camera-a", "camera-b"] and not api.saved


def test_failed_start_drops_one_camera_and_still_stops_its_uncertain_recorder(monkeypatch):
    api = FakeCaptureAPI("startup")
    monkeypatch.setattr(cache, "call", api)
    monkeypatch.setattr(cache, "time", FakeClock())
    kept, dropped = cache.cache_video("fixture", [{"id": "camera-a"}, {"id": "camera-b"}], cache_args())
    assert set(kept) == {"camera-a"} and set(dropped) == {"camera-b"}
    assert not api.active and api.stopped == ["camera-a", "camera-b"]


def test_transient_poll_failure_recovers_without_losing_final_revision(monkeypatch):
    api, failed = FakeCaptureAPI(), False
    def call(base, path, body=None, **kwargs):
        nonlocal failed
        if path.endswith("/buffer") and not failed:
            failed = True
            raise RuntimeError("transient poll failure")
        return api(base, path, body, **kwargs)
    monkeypatch.setattr(cache, "call", call)
    monkeypatch.setattr(cache, "time", FakeClock())
    kept, dropped = cache.cache_video("fixture", [{"id": "camera-a"}], cache_args())
    assert set(kept) == {"camera-a"} and not dropped
    assert kept["camera-a"]["firstMediaS"] == 4
    assert api.saved[0]["bufferRevision"] == "camera-a-final"


def test_failed_stop_does_not_prevent_other_stops_or_seal_uncertain_source(monkeypatch):
    api = FakeCaptureAPI("stop")
    monkeypatch.setattr(cache, "call", api)
    monkeypatch.setattr(cache, "time", FakeClock())
    kept, dropped = cache.cache_video("fixture", [{"id": "camera-a"}, {"id": "camera-b"}], cache_args())
    assert set(kept) == {"camera-b"} and set(dropped) == {"camera-a"}
    assert api.stopped == ["camera-a", "camera-b"] and len(api.saved) == 1


def test_slow_later_stop_does_not_lower_an_already_stopped_cameras_rate(monkeypatch):
    clock, api = FakeClock(), FakeCaptureAPI()
    def call(base, path, body=None, **kwargs):
        if path == "/api/captures/camera-b/stop":
            clock.sleep(60)
        return api(base, path, body, **kwargs)
    monkeypatch.setattr(cache, "call", call)
    monkeypatch.setattr(cache, "time", clock)
    kept, dropped = cache.cache_video("fixture", [{"id": "camera-a"}, {"id": "camera-b"}], cache_args())
    assert kept["camera-a"]["realtimeRatio"] == 1.5
    assert "camera-b" in dropped and len(api.saved) == 1


def test_segment_arrival_slack_admits_boundary_feed_but_preserves_measured_rate(monkeypatch):
    api = FakeCaptureAPI()
    def call(base, path, body=None, **kwargs):
        result = api(base, path, body, **kwargs)
        if path.endswith(("/buffer", "/stop")):
            # First media at t=2; only one 2-second segment arrives by the t=6
            # stop. Measured rate is 2/4=.5, but one-segment slack admits 4/4=1.
            buffered = 12 if path.endswith("/stop") else 10
            result["bufferedSeconds"] = buffered
            result["history"]["segmentBoundariesS"] = list(range(0, buffered + 1, 2))
        return result
    monkeypatch.setattr(cache, "call", call)
    monkeypatch.setattr(cache, "time", FakeClock())
    kept, dropped = cache.cache_video("fixture", [{"id": "camera-a"}], cache_args())
    assert set(kept) == {"camera-a"} and not dropped and len(api.saved) == 1
    assert kept["camera-a"]["realtimeRatio"] == .5
    assert kept["camera-a"]["realtimeRatio"] < cache_args().min_realtime_ratio


def test_cache_writer_persists_only_extraction_identifier(tmp_path, monkeypatch):
    entry = {"kind": "hls", "saved": {"capture": {"id": "capture-fixture"}}}
    monkeypatch.setattr(cache, "OUT", tmp_path / "demo-cache.json")
    monkeypatch.setattr(cache, "cache_video", lambda *args: ({"camera-a": entry}, {}))
    def call(base, path, body=None, **kwargs):
        if path == "/api/observations/extract":
            assert kwargs["token"] == "fixture-token"
            return {"id": "extract_fixture", "observations": ["operator-only fixture"]}
        assert path in ("/api/health", "/api/vision/cameras")
        return []
    monkeypatch.setattr(cache, "call", call)
    monkeypatch.setattr(cache.sys, "argv", ["cache", "--token", "fixture-token", "--skip-snapshots"])
    assert cache.main() == 0
    payload = json.loads(cache.OUT.read_text())
    assert payload["cameras"]["camera-a"]["extractionId"] == "extract_fixture"
    assert "operator-only fixture" not in cache.OUT.read_text()
