import asyncio
from copy import deepcopy
import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.contracts import CaptureContext, EvidenceRef, ImageRegion
from backend.gateway import GatewayError, GatewayResult
from backend.observations import ExtractionOutput, ObservationError, ObservationRequest, ObservationService, provider_schema, router

CAPTURE = {"captureId": "cap_fixture", "sourceId": "511ny-32998", "originalMediaSha256": "a" * 64,
           "modality": "video", "durationSeconds": 10, "allowedInterval": {"startSeconds": 0, "endSeconds": 5},
           "receivedAt": "2026-11-21T15:00:00Z", "sourceCapturedAt": None, "sourceClock": "unknown", "clockUncertaintySeconds": None}
TRACE = {"traceId": "trace_fixture", "requestId": "request_fixture", "providerRequestId": None,
         "modelId": "gemini-4-hydrogen", "promptId": "prompt_fixture", "configId": "config_fixture", "inputSha256": "b" * 64,
         "startedAt": "2026-11-21T15:00:00Z", "completedAt": "2026-11-21T15:00:01Z", "outcome": "success",
         "inputTokens": 100, "outputTokens": 500}


def observation(**changes):
    return {"category": "crossing", "feature": "crossing_markings", "state": "visible", "countLower": None,
            "countUpper": None, "confidence": .8, "unknownReason": None, "provenance": "observed",
            "evidence": [{"frame": 0, "region": {"x": .1, "y": .2, "width": .2, "height": .3}}], "eventType": None, **changes}


class Captures:
    def __init__(self, snapshot=False):
        value = deepcopy(CAPTURE)
        if snapshot:
            value.update(modality="snapshot", durationSeconds=0, allowedInterval={"startSeconds": 0, "endSeconds": 0})
        self.capture = CaptureContext.model_validate(value)
        self.pins = []

    def capture_context(self, capture_id):
        assert capture_id == self.capture.captureId
        return self.capture

    def pin(self, capture_id, job_id):
        self.pins.append((capture_id, job_id))


class Frame:
    def __init__(self, capture, time, index, crop):
        self.capture, self.time_seconds, self.frame_index, self.region = capture, time, index, crop
        self.data, self.mime_type = b"fixture-source-image-" + str(index).encode(), "image/jpeg"

    def to_evidence_ref(self, region=None):
        roi = self.region
        if region:
            roi = ImageRegion(x=roi.x + region.x * roi.width, y=roi.y + region.y * roi.height,
                              width=region.width * roi.width, height=region.height * roi.height)
        return EvidenceRef(captureId=self.capture.captureId, originalMediaSha256=self.capture.originalMediaSha256,
                           timeSeconds=self.time_seconds, frameIndex=self.frame_index, region=roi)


class Prepared:
    def __init__(self, capture, frames):
        self.capture, self.frames = capture, frames

    def metadata(self):
        return {"frameCount": len(self.frames), "frames": [{"timeSeconds": f.time_seconds,
                 "sha256": hashlib.sha256(f.data).hexdigest()} for f in self.frames]}


async def prepare(captures, capture_id, *, window=None, crop=None, **kwargs):
    capture = captures.capture_context(capture_id)
    roi = crop or ImageRegion(x=0, y=0, width=1, height=1)
    times = [0] if capture.modality == "snapshot" else [0, 1, 2, 3, 4]
    if window:
        times = [t for t in times if window.startSeconds <= t < window.endSeconds]
        capture = capture.model_copy(update={"allowedInterval": window})
    return Prepared(capture, [Frame(capture, time, index, roi) for index, time in enumerate(times)])


class Gateway:
    def __init__(self, observations=None, error=None):
        self.observations, self.error, self.calls = observations or [observation()], error, []

    async def run(self, service, payload, **kwargs):
        self.calls.append((service, payload, kwargs))
        if self.error:
            raise self.error
        output = {"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text",
                  "text": json.dumps({"observations": self.observations, "unknowns": ["Signal timing is unknown."]})}]}]}
        return GatewayResult(output, TRACE.copy(), {})


def service(tmp_path, *, snapshot=False, observations=None, error=None):
    return ObservationService(Captures(snapshot), Gateway(observations, error), tmp_path,
                              evidence_preparer=prepare)


def test_observations_use_server_evidence_real_trace_and_persist_across_restart(tmp_path):
    async def run():
        worker = service(tmp_path)
        result = await worker.create(ObservationRequest(captureId="cap_fixture"))
        assert result.capture.originalMediaSha256 == CAPTURE["originalMediaSha256"]
        assert result.trace.modelId == "gemini-4-hydrogen"
        assert result.observations[0].value == "crossing_markings: visible"
        assert worker.captures.pins
        payload = worker.gateway.calls[0][1]
        assert payload["response_format"]["schema"]
        assert sum(item["type"] == "image" for item in payload["input"]) == 5
        assert "event" not in payload["response_format"]["schema"]["$defs"]["ExtractedObservation"]["properties"]["category"]["enum"]
        restarted = ObservationService(worker.captures, worker.gateway, tmp_path, evidence_preparer=prepare)
        assert restarted.get(result.id) == result
        attempt = restarted.attempt(restarted.evidence(result.id)["attemptId"])
        assert attempt["status"] == "accepted"
        assert attempt["trace"]["requestId"] == "request_fixture"
    asyncio.run(run())


def test_crop_reinspection_maps_actual_frame_regions_to_original_image(tmp_path):
    async def run():
        worker = service(tmp_path)
        result = await worker.create(ObservationRequest(captureId="cap_fixture", crop=ImageRegion(x=.25,y=.2,width=.5,height=.5),
                                                       window={"startSeconds": 2, "endSeconds": 4}))
        evidence = result.observations[0].evidence[0]
        assert evidence.timeSeconds == 2
        assert evidence.region.x == pytest.approx(.3)
        assert evidence.region.y == pytest.approx(.3)
        assert evidence.region.width == pytest.approx(.1)
        assert worker.get(result.id).capture.allowedInterval.startSeconds == 2
    asyncio.run(run())


@pytest.mark.parametrize("bad", [
    observation(evidence=[{"frame": 999, "region": None}]),
    observation(category="event", eventType="entry", evidence=[{"frame": 0, "region": None}, {"frame": 1, "region": None}]),
    observation(state="unknown", unknownReason=None),
    observation(category="count", feature="vehicle", countLower=3, countUpper=1),
    {**observation(), "value": "Traffic flows at 15 vehicles per minute"},
    observation(state="moving"),
    observation(evidence=[{"frame": 0, "region": {"x": .9, "y": 0, "width": .5, "height": .5}}]),
])
def test_invalid_outputs_are_rejected_and_attempts_preserved(tmp_path, bad):
    async def run():
        worker = service(tmp_path, observations=[bad])
        with pytest.raises(ObservationError) as caught:
            await worker.create(ObservationRequest(captureId="cap_fixture"))
        record = worker.attempt(caught.value.attempt_id)
        assert record["status"] == "rejected"
        assert record["trace"] == TRACE
        assert not list((tmp_path / "artifacts").glob("*.json"))
    asyncio.run(run())


def test_snapshot_supports_instantaneous_counts_but_rejects_temporal_events(tmp_path):
    async def run():
        worker = service(tmp_path, snapshot=True, observations=[observation(category="count", feature="vehicle", countLower=2, countUpper=4)])
        result = await worker.create(ObservationRequest(captureId="cap_fixture"))
        assert "instantaneous visible count [2, 4]" in result.observations[0].value
        assert result.observations[0].evidence[0].timeSeconds == 0
        with pytest.raises(ObservationError, match="invalid_event_interval"):
            await worker.create(ObservationRequest(captureId="cap_fixture", includeEvents=True, ownedInterval={"startSeconds": 0, "endSeconds": 1}))
    asyncio.run(run())


def test_events_have_non_overlapping_persistent_ownership_and_no_duplicate_ids(tmp_path):
    event = observation(category="event", feature="vehicle", eventType="crossing",
                        evidence=[{"frame": 0, "region": None}, {"frame": 1, "region": None}])
    async def run():
        worker = service(tmp_path, observations=[event, event])
        request = ObservationRequest(captureId="cap_fixture", includeEvents=True, ownedInterval={"startSeconds": 0, "endSeconds": 2})
        result = await worker.create(request)
        assert len(result.observations) == 1
        restarted = ObservationService(worker.captures, worker.gateway, tmp_path, evidence_preparer=prepare)
        with pytest.raises(ObservationError, match="event_interval_owned"):
            await restarted.create(request)
        assert len(worker.gateway.calls) == 1
    asyncio.run(run())


def test_event_crossing_owned_interval_boundary_is_rejected(tmp_path):
    event = observation(category="event", feature="vehicle", eventType="crossing",
                        evidence=[{"frame": 0, "region": None}, {"frame": 2, "region": None}])
    async def run():
        worker = service(tmp_path, observations=[event])
        with pytest.raises(ObservationError, match="unowned_temporal_event"):
            await worker.create(ObservationRequest(captureId="cap_fixture", includeEvents=True, ownedInterval={"startSeconds": 0, "endSeconds": 2}))
    asyncio.run(run())


def test_failures_are_sanitized_and_retain_model_trace_id(tmp_path):
    async def run():
        failure = GatewayError("quota", trace_id="trace_failed")
        worker = service(tmp_path, error=failure)
        with pytest.raises(ObservationError) as caught:
            await worker.create(ObservationRequest(captureId="cap_fixture"))
        value = worker.attempt(caught.value.attempt_id)
        assert value["error"] == {"code": "model_quota"}
        assert value["gatewayTraceId"] == "trace_failed"
    asyncio.run(run())


def test_observation_router_returns_artifact_and_blocks_client_supplied_provenance(tmp_path):
    app = FastAPI()
    app.state.observations = service(tmp_path)
    app.include_router(router)
    with TestClient(app) as client:
        rejected = client.post('/api/observations', json={"captureId": "cap_fixture", "trace": TRACE})
        assert rejected.status_code == 422
        accepted = client.post('/api/observations', json={"captureId": "cap_fixture"})
        assert accepted.status_code == 201
        assert accepted.headers['X-Observation-Attempt-Id']
        artifact = accepted.json()
        assert client.get('/api/observations/' + artifact['id']).json() == artifact
        assert client.get('/api/observations/' + artifact['id'] + '/evidence').status_code == 200
        cropped = client.post('/api/observations', json={"captureId": "cap_fixture", "window": {"startSeconds": 2, "endSeconds": 4}})
        assert cropped.status_code == 201
        narrowed = cropped.json()
        assert narrowed['capture']['allowedInterval'] == {'startSeconds': 2, 'endSeconds': 4}
        assert client.get('/api/observations/' + narrowed['id']).json() == narrowed


def test_model_cannot_reference_frames_outside_targeted_window(tmp_path):
    async def run():
        worker = service(tmp_path, observations=[observation(evidence=[{'frame': 2, 'region': None}])])
        with pytest.raises(ObservationError, match='unknown_evidence_frame'):
            await worker.create(ObservationRequest(captureId='cap_fixture', window={'startSeconds': 2, 'endSeconds': 4}))
        assert not list((tmp_path / 'artifacts').glob('*.json'))
    asyncio.run(run())


def test_structured_schema_cannot_encode_flow_or_trajectory_values():
    schema = ExtractionOutput.model_json_schema()['$defs']['ExtractedObservation']['properties']
    assert 'value' not in schema
    assert 'description' not in schema
    assert schema['state']['enum'] == ['visible','partially_occluded','occluded','unknown','red','amber','green','unlit']


def test_provider_grammar_projection_removes_bounds_but_local_validation_retains_them():
    from pydantic import ValidationError
    original = ExtractionOutput.model_json_schema()
    projected = provider_schema(original)
    serialized = json.dumps(projected)
    for keyword in ('minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'minItems', 'maxItems', 'minLength', 'maxLength', 'title'):
        assert f'"{keyword}"' not in serialized
    assert projected['$defs']['ExtractedObservation']['properties']['state']['enum'] == original['$defs']['ExtractedObservation']['properties']['state']['enum']
    assert projected['properties']['observations']['items']['$ref']
    assert projected['required'] == ['observations', 'unknowns']
    assert original['properties']['observations']['maxItems'] == 16
    for observations in ([observation()] * 17, [observation(countLower=-1, countUpper=1, category='count')],
                         [observation(evidence=[{'frame': 0, 'region': {'x': 0, 'y': 0, 'width': 0, 'height': 1}}])]):
        with pytest.raises(ValidationError):
            ExtractionOutput.model_validate({'observations': observations, 'unknowns': []})


def test_preprocessing_concurrency_is_bounded_and_cancel_releases_slot(tmp_path):
    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()
        async def slow_prepare(*args, **kwargs):
            entered.set()
            await release.wait()
            return await prepare(*args, **kwargs)
        worker = ObservationService(Captures(), Gateway(), tmp_path, evidence_preparer=slow_prepare, concurrency=1)
        first = asyncio.create_task(worker.create(ObservationRequest(captureId="cap_fixture")))
        await entered.wait()
        with pytest.raises(ObservationError, match="busy") as blocked:
            await worker.create(ObservationRequest(captureId="cap_fixture"))
        assert worker.attempt(blocked.value.attempt_id)["error"]["code"] == "busy"
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        result = await worker.create(ObservationRequest(captureId="cap_fixture"))
        assert result.observations
        assert len(worker.gateway.calls) == 1
        attempts = [json.loads(path.read_text()) for path in (tmp_path / 'attempts').glob('*.json')]
        assert {record['status'] for record in attempts} == {'cancelled', 'rejected', 'accepted'}
    asyncio.run(run())


def test_occlusion_produces_explicit_unknown_instead_of_actor_disappearance(tmp_path):
    async def run():
        worker = service(tmp_path, observations=[observation(category='signal', feature='traffic_signal', state='occluded',
                         confidence=None, unknownReason='The signal face is hidden behind a truck.')])
        result = await worker.create(ObservationRequest(captureId='cap_fixture'))
        assert result.observations[0].value is None
        assert result.observations[0].unknownReason
        assert result.observations[0].evidence
    asyncio.run(run())


@pytest.mark.parametrize('field', ['evidence', 'attemptId'])
def test_envelope_hash_covers_source_metadata_and_attempt_link(tmp_path, field):
    async def run():
        worker = service(tmp_path)
        artifact = await worker.create(ObservationRequest(captureId='cap_fixture'))
        path = tmp_path / 'artifacts' / f'{artifact.id}.json'
        value = json.loads(path.read_text())
        value[field] = {'frameCount': 999} if field == 'evidence' else 'attempt_other'
        path.write_text(json.dumps(value))
        with pytest.raises(ObservationError, match='integrity'):
            worker.get(artifact.id)
    asyncio.run(run())


def test_restart_recovers_finished_artifact_but_releases_interrupted_event_owner(tmp_path):
    event = observation(category='event', feature='vehicle', eventType='crossing',
                        evidence=[{'frame': 0, 'region': None}, {'frame': 1, 'region': None}])
    async def run():
        worker = service(tmp_path, observations=[event])
        request = ObservationRequest(captureId='cap_fixture', includeEvents=True, ownedInterval={'startSeconds': 0, 'endSeconds': 2})
        artifact = await worker.create(request)
        attempt_id = worker.evidence(artifact.id)['attemptId']
        record = worker.attempt(attempt_id)
        record.update(status='running', artifactId=None, completedAt=None)
        worker._record(record)
        interrupted = deepcopy(record)
        interrupted.update(attemptId='attempt_interrupted', trace=None)
        interrupted['request']['ownedInterval'] = {'startSeconds': 2, 'endSeconds': 4}
        worker._record(interrupted)
        restarted = ObservationService(worker.captures, worker.gateway, tmp_path, evidence_preparer=prepare)
        assert restarted.attempt(attempt_id)['status'] == 'accepted'
        assert restarted.attempt(attempt_id)['artifactId'] == artifact.id
        assert restarted.attempt('attempt_interrupted')['status'] == 'interrupted'
        with pytest.raises(ObservationError, match='event_interval_owned'):
            await restarted.create(request)
        restarted.gateway.observations = [observation()]
        second = await restarted.create(ObservationRequest(captureId='cap_fixture', includeEvents=True, ownedInterval={'startSeconds': 2, 'endSeconds': 4}))
        assert second.id != artifact.id
    asyncio.run(run())
