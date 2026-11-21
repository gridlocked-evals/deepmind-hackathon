"""Cache metadata and recorder lifecycle regressions; all transport is in-process or fake."""
import json
from types import SimpleNamespace
from threading import Event, Lock, get_ident, local

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
    def __init__(self):
        self.thread = local()

    @property
    def now(self):
        return getattr(self.thread, "now", 0.0)

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.thread.now = self.now + seconds


@pytest.fixture(autouse=True)
def virtual_recorder_wait(monkeypatch):
    class VirtualEvent:
        def __init__(self):
            self.event = Event()

        def set(self):
            self.event.set()

        def is_set(self):
            return self.event.is_set()

        def wait(self, seconds):
            if not self.is_set():
                cache.time.sleep(seconds)
            return self.is_set()

    monkeypatch.setattr(cache, "Event", VirtualEvent, raising=False)


class FakeCaptureAPI:
    def __init__(self, failure=None):
        self.active = set()
        self.started = []
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
            self.started.append(camera_id)
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
    assert set(api.stopped) == set(camera_ids) and len(api.saved) == len(camera_ids)
    assert all(body["secondsAgo"] == body["durationS"] == 6 for body in api.saved)


@pytest.mark.parametrize("failure", ["startup_fatal", "poll_fatal"])
def test_video_cache_cleans_up_recorders_after_partial_failure(monkeypatch, failure):
    api = FakeCaptureAPI(failure)
    monkeypatch.setattr(cache, "call", api)
    monkeypatch.setattr(cache, "time", FakeClock())
    with pytest.raises((ValueError, KeyError)):
        cache.cache_video("fixture", [{"id": "camera-a"}, {"id": "camera-b"}], cache_args())
    assert not api.active and set(api.stopped) == set(api.started) and not api.saved


def test_failed_start_drops_one_camera_and_still_stops_its_uncertain_recorder(monkeypatch):
    api = FakeCaptureAPI("startup")
    monkeypatch.setattr(cache, "call", api)
    monkeypatch.setattr(cache, "time", FakeClock())
    kept, dropped = cache.cache_video("fixture", [{"id": "camera-a"}, {"id": "camera-b"}], cache_args())
    assert set(kept) == {"camera-a"} and set(dropped) == {"camera-b"}
    assert not api.active and set(api.stopped) == {"camera-a", "camera-b"}


@pytest.mark.parametrize("camera_ids", [("camera-a", "camera-b"), ("camera-b", "camera-a")])
def test_slow_failed_start_does_not_consume_healthy_cameras_observation_interval(monkeypatch, camera_ids):
    api, clock = FakeCaptureAPI(), FakeClock()
    slow_start, healthy_poll = Event(), Event()
    observed_during_start = []
    def call(base, path, body=None, **kwargs):
        if path == "/api/captures/camera-b/recording":
            api.active.add("camera-b")
            slow_start.set()
            observed_during_start.append(healthy_poll.wait(2))
            clock.sleep(60)  # lost start response takes longer than the entire recording period
            raise TimeoutError("start response lost after admission")
        if path == "/api/captures/camera-a/recording":
            assert slow_start.wait(2), "the other startup was not dispatched independently"
        if path == "/api/captures/camera-a/buffer":
            healthy_poll.set()
        return api(base, path, body, **kwargs)
    monkeypatch.setattr(cache, "call", call)
    monkeypatch.setattr(cache, "time", clock)
    kept, dropped = cache.cache_video("fixture", [{"id": cid} for cid in camera_ids], cache_args())
    assert set(kept) == {"camera-a"} and set(dropped) == {"camera-b"}
    assert api.polls["camera-a"] >= 2
    assert kept["camera-a"]["firstMediaS"] == 2
    assert kept["camera-a"]["realtimeRatio"] == 1.5
    assert observed_during_start == [True]
    assert not api.active and set(api.stopped) == set(camera_ids)


def test_slow_successful_start_still_counts_toward_first_media_latency(monkeypatch):
    api, clock = FakeCaptureAPI(), FakeClock()
    def call(base, path, body=None, **kwargs):
        if path.endswith("/recording"):
            clock.sleep(20)
        return api(base, path, body, **kwargs)
    monkeypatch.setattr(cache, "call", call)
    monkeypatch.setattr(cache, "time", clock)
    kept, dropped = cache.cache_video("fixture", [{"id": "camera-a"}], cache_args())
    assert not kept and dropped == {"camera-a": "first media after 22 s"}
    assert api.polls["camera-a"] >= 2 and not api.active


def test_video_cancellation_wakes_workers_and_joins_all_stops_before_return(monkeypatch):
    api, waiting, lock = FakeCaptureAPI(), set(), Lock()
    all_waiting = Event()
    class ControlledEvent:
        def __init__(self):
            self.event = Event()

        def set(self):
            self.event.set()

        def is_set(self):
            return self.event.is_set()

        def wait(self, seconds):
            with lock:
                waiting.add(get_ident())
                if len(waiting) == 4:
                    all_waiting.set()
            assert self.event.wait(2), "main cancellation failed to wake a recording worker"
            return True

    original_pool = cache.ThreadPoolExecutor
    class InterruptingPool(original_pool):
        def map(self, function, values):
            super().map(function, values)  # dispatch real workers, then interrupt result consumption
            assert all_waiting.wait(2), "all four bounded workers must be waiting before interruption"
            raise KeyboardInterrupt

    monkeypatch.setattr(cache, "Event", ControlledEvent)
    monkeypatch.setattr(cache, "ThreadPoolExecutor", InterruptingPool)
    monkeypatch.setattr(cache, "time", FakeClock())
    monkeypatch.setattr(cache, "call", api)
    with pytest.raises(KeyboardInterrupt):
        cache.cache_video("fixture", [{"id": f"camera-{i}"} for i in range(6)], cache_args())
    assert set(api.started) == set(api.stopped) == {f"camera-{i}" for i in range(4)}
    assert not api.active and not api.saved and not api.polls


def test_empty_video_list_makes_no_recorder_requests(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("empty list must not dispatch workers or requests")
    monkeypatch.setattr(cache, "call", unexpected)
    monkeypatch.setattr(cache, "ThreadPoolExecutor", unexpected)
    kept = {"existing": {"kind": "snapshot"}}
    result, dropped = cache.cache_video("fixture", [], cache_args(), kept)
    assert result is kept and not dropped


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
    assert set(api.stopped) == {"camera-a", "camera-b"} and len(api.saved) == 1


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
