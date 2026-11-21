"""Driver admission and transport tests; never use a provider or credentials."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from backend.driver import (
    DriverController, DriverDecision, DriverRequest, MAX_REQUEST_BYTES, MAX_STEPS,
    RUN_TTL_SECONDS, router,
)
from backend.gateway.contracts import ErrorCode, GatewayError, MODELS, Service


def request_body(step=0, run_id="test-run-0001"):
    return {
        "run_id": run_id, "step": step,
        "observation": {
            "scenario_id": "midtown", "simulation_time": 42.0, "speed_mps": 4.0,
            "speed_limit_mps": 11.176, "remaining_m": 600.0, "signal": "green",
            "stop_distance_m": 80.0, "lead_gap_m": None, "lead_speed_mps": None,
            "upcoming_turn_degrees": 0.0, "route_label": "Broadway to 42nd Street",
        },
    }


class FakeGateway:
    def __init__(self):
        self.calls = []
        self.error = None
        self.output = {"action": "accelerate", "target_speed_mps": 6.0,
                       "reason": "The route is clear and the signal is green.", "confidence": 0.9}
        self.release = None

    async def generate(self, service, **kwargs):
        self.calls.append((service, kwargs))
        if self.release:
            await self.release.wait()
        if self.error:
            raise self.error
        return SimpleNamespace(output=deepcopy(self.output), model_id=MODELS[service], request_id=str(uuid4()))


def client_for(gateway=None):
    gateway = gateway or FakeGateway()
    app = FastAPI()
    app.state.driver = DriverController(gateway)
    app.include_router(router)
    return TestClient(app), gateway, app.state.driver


def test_decision_uses_existing_gateway_and_returns_real_trace():
    client, gateway, _ = client_for()
    body = request_body()
    response = client.post("/api/driver/decision", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["run_id"] == body["run_id"] and result["step"] == 0
    assert result["max_steps"] == 12 and result["model_id"] == "gemini-4-hydrogen"
    assert result["request_id"] and result["decision"] == gateway.output
    assert len(gateway.calls) == 1
    service, call = gateway.calls[0]
    assert service == Service.HYDROGEN and call["stage"] == "navigation"
    assert call["response_model"] is DriverDecision
    context = call["context"]
    assert context.purpose == "development" and context.source_id is None and context.capture_id is None
    assert context.episode_id == "sim-driver:test-run-0001"
    assert context.prompt_id == "sim-driver-v2:step-0"
    assert "synthetic traffic simulator" in call["input_value"]
    assert json.loads(call["input_value"].split("Observation JSON:\n")[1]) == body["observation"]


@pytest.mark.parametrize("path,value", [
    (("step",), -1), (("step",), 12), (("step",), True), (("step",), "0"),
    (("run_id",), "short"), (("run_id",), "x" * 65), (("run_id",), "../a/b/c"),
    (("extra",), "x"), (("observation", "extra"), "x"),
    (("observation", "scenario_id"), "x" * 81),
    (("observation", "route_label"), "x" * 161),
    (("observation", "simulation_time"), -1),
    (("observation", "simulation_time"), 86401),
    (("observation", "speed_mps"), -1), (("observation", "speed_mps"), True),
    (("observation", "speed_mps"), "4"), (("observation", "speed_mps"), 51),
    (("observation", "speed_limit_mps"), 0),
    (("observation", "remaining_m"), 100001),
    (("observation", "signal"), "purple"),
    (("observation", "stop_distance_m"), -1),
    (("observation", "lead_gap_m"), 10001),
    (("observation", "lead_speed_mps"), 51),
    (("observation", "upcoming_turn_degrees"), 181),
])
def test_request_boundaries_reject_before_gateway(path, value):
    client, gateway, _ = client_for()
    body = request_body()
    target = body if len(path) == 1 else body[path[0]]
    target[path[-1]] = value
    response = client.post("/api/driver/decision", json=body)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_input"
    assert not gateway.calls


@pytest.mark.parametrize("raw", [
    "{", "[]", "null", '{"step":0,"step":1}',
    json.dumps(request_body()).replace('"speed_mps": 4.0', '"speed_mps": NaN'),
    json.dumps(request_body()).replace('"speed_mps": 4.0', '"speed_mps": Infinity'),
    json.dumps(request_body()).replace('"speed_mps": 4.0', '"speed_mps": 1e999'),
])
def test_invalid_json_and_nonfinite_numbers_are_fixed_errors(raw):
    client, gateway, _ = client_for()
    response = client.post("/api/driver/decision", content=raw, headers={"content-type": "application/json"})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_input"
    assert not gateway.calls


def test_body_limit_enforced_with_content_length_and_streamed_body():
    client, gateway, _ = client_for()
    data = b" " * (MAX_REQUEST_BYTES + 1)
    response = client.post("/api/driver/decision", content=data, headers={"content-type": "application/json"})
    assert response.status_code == 413
    # A forged small header must not bypass the streaming limit.
    response = client.post("/api/driver/decision", content=iter((data[:4000], data[4000:])),
                           headers={"content-type": "application/json", "content-length": "1"})
    assert response.status_code == 413
    assert client.post("/api/driver/decision", content="{}").status_code == 415
    assert not gateway.calls


def test_duplicate_idempotency_and_twelve_step_limit():
    client, gateway, _ = client_for()
    first = client.post("/api/driver/decision", json=request_body())
    duplicate = client.post("/api/driver/decision", json=request_body())
    assert duplicate.json() == first.json() and len(gateway.calls) == 1
    changed = request_body()
    changed["observation"]["speed_mps"] = 2.0
    assert client.post("/api/driver/decision", json=changed).status_code == 409
    assert client.post("/api/driver/decision", json=request_body(2)).status_code == 409
    for step in range(1, MAX_STEPS):
        assert client.post("/api/driver/decision", json=request_body(step)).status_code == 200
    assert len(gateway.calls) == MAX_STEPS
    assert client.post("/api/driver/decision", json=request_body(MAX_STEPS)).status_code == 422
    assert client.post("/api/driver/decision", json=request_body()).json() == first.json()
    assert len(gateway.calls) == MAX_STEPS


def test_runs_must_start_at_zero_and_keep_their_scenario():
    client, gateway, _ = client_for()
    assert client.post("/api/driver/decision", json=request_body(1)).status_code == 409
    assert not gateway.calls
    assert client.post("/api/driver/decision", json=request_body()).status_code == 200
    changed = request_body(1)
    changed["observation"]["scenario_id"] = "brooklyn"
    assert client.post("/api/driver/decision", json=changed).status_code == 409
    assert len(gateway.calls) == 1


@pytest.mark.parametrize("change", [
    {"target_speed_mps": -1.0}, {"target_speed_mps": 12.0},
    {"target_speed_mps": float("nan")}, {"target_speed_mps": float("inf")},
    {"target_speed_mps": "5"}, {"target_speed_mps": True},
    {"confidence": 1.01}, {"confidence": float("nan")},
    {"reason": "x" * 241}, {"action": "teleport"}, {"extra": "discard me"},
    {"action": "stop", "target_speed_mps": 1.0},
    {"action": "brake", "target_speed_mps": 5.0},
])
def test_invalid_provider_decisions_fail_without_a_fake_fallback(change):
    gateway = FakeGateway()
    gateway.output.update(change)
    client, _, _ = client_for(gateway)
    response = client.post("/api/driver/decision", json=request_body())
    assert response.status_code == 502 and response.json()["detail"]["code"] == "invalid_output"
    assert "decision" not in response.json()
    assert client.post("/api/driver/decision", json=request_body()).json() == response.json()
    assert len(gateway.calls) == 1


def test_provider_cannot_exceed_observed_speed_limit():
    client, gateway, _ = client_for()
    body = request_body()
    body["observation"]["speed_limit_mps"] = 5.0
    response = client.post("/api/driver/decision", json=body)
    assert response.status_code == 502 and response.json()["detail"]["code"] == "invalid_output"


@pytest.mark.parametrize("error,status,code", [
    (GatewayError(ErrorCode.NOT_CONFIGURED), 503, "not_configured"),
    (GatewayError(ErrorCode.AUTH), 503, "authentication_failed"),
    (GatewayError(ErrorCode.BUDGET), 429, "budget_exceeded"),
    (GatewayError(ErrorCode.TIMEOUT, retryable=True), 504, "timeout"),
    (RuntimeError("upstream leaked secret-test-credential"), 502, "upstream_error"),
])
def test_failures_are_redacted_cached_and_consume_a_step(error, status, code):
    gateway = FakeGateway()
    gateway.error = error
    client, _, controller = client_for(gateway)
    response = client.post("/api/driver/decision", json=request_body())
    assert response.status_code == status and response.json()["detail"]["code"] == code
    assert "secret-test-credential" not in response.text
    assert client.post("/api/driver/decision", json=request_body()).json() == response.json()
    assert len(gateway.calls) == 1
    gateway.error = None
    assert client.post("/api/driver/decision", json=request_body(1)).status_code == 200
    assert len(gateway.calls) == 2 and controller.in_flight == 0


def test_untrusted_exception_message_and_request_id_never_escape():
    gateway = FakeGateway()
    gateway.error = GatewayError(ErrorCode.UPSTREAM, request_id="secret-test-credential")
    gateway.error.args = ("upstream leaked secret-test-credential",)
    client, _, _ = client_for(gateway)
    response = client.post("/api/driver/decision", json=request_body())
    assert response.status_code == 502 and "secret-test-credential" not in response.text
    assert response.json()["detail"]["request_id"] is None


def test_concurrency_limits_reject_without_dispatch_and_recover():
    async def scenario():
        gateway = FakeGateway()
        gateway.release = asyncio.Event()
        controller = DriverController(gateway)
        one = DriverRequest.model_validate(request_body())
        two = DriverRequest.model_validate(request_body(run_id="test-run-0002"))
        tasks = [asyncio.create_task(controller.decide(one)), asyncio.create_task(controller.decide(two))]
        await asyncio.sleep(0)
        assert len(gateway.calls) == 2 and controller.in_flight == 2
        for body in (one, DriverRequest.model_validate(request_body(1)),
                     DriverRequest.model_validate(request_body(run_id="test-run-0003"))):
            with pytest.raises(HTTPException) as exc:
                await controller.decide(body)
            assert exc.value.status_code == 429 and exc.value.detail["code"] == "concurrency_limit"
        assert len(gateway.calls) == 2
        gateway.release.set()
        results = await asyncio.gather(*tasks)
        assert controller.in_flight == 0
        assert await controller.decide(one) == results[0]
        assert len(gateway.calls) == 2
    asyncio.run(scenario())


def test_cancellation_releases_slots_and_prevents_duplicate_bill():
    async def scenario():
        gateway = FakeGateway()
        gateway.release = asyncio.Event()
        controller = DriverController(gateway)
        body = DriverRequest.model_validate(request_body())
        task = asyncio.create_task(controller.decide(body))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert controller.in_flight == 0
        with pytest.raises(HTTPException) as exc:
            await controller.decide(body)
        assert exc.value.detail["code"] == "cancelled" and len(gateway.calls) == 1
    asyncio.run(scenario())


def test_run_capacity_and_expiry_do_not_evict_inflight(monkeypatch):
    monkeypatch.setattr("backend.driver.MAX_RUNS", 2)

    async def scenario():
        now = [0.0]
        gateway = FakeGateway()
        controller = DriverController(gateway, clock=lambda: now[0])
        await controller.decide(DriverRequest.model_validate(request_body()))
        gateway.release = asyncio.Event()
        second = asyncio.create_task(controller.decide(DriverRequest.model_validate(request_body(run_id="test-run-0002"))))
        await asyncio.sleep(0)
        third = DriverRequest.model_validate(request_body(run_id="test-run-0003"))
        with pytest.raises(HTTPException) as exc:
            await controller.decide(third)
        assert exc.value.status_code == 429 and len(gateway.calls) == 2
        now[0] = RUN_TTL_SECONDS + 1
        gateway.release.set()
        # The expired completed run is reaped; the in-flight run is retained.
        await controller.decide(third)
        assert set(controller.runs) == {"test-run-0002", "test-run-0003"}
        await second
        assert controller.in_flight == 0
    asyncio.run(scenario())
