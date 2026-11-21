"""Driver admission and transport tests; never use a provider or credentials."""
import asyncio
from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
import httpx

from backend.gateway import GatewayConfig, ModelGateway
from backend.gateway.config import StageBudget

from backend.driver import (
    DriverController, DriverRequest, MAX_REQUEST_BYTES, MAX_STEPS,
    RUN_TTL_SECONDS, router,
)
from backend.gateway.contracts import ErrorCode, GatewayError, MODELS, Service, STAGES


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


def choice_output(choice="accelerate", *, go_around=False):
    actions = ("stop", "brake", "cruise", "accelerate") + (("go_around",) if go_around else ())
    return {"maneuver": {"type": "choice", "choice": choice, "confidence": 0.9,
                        "probabilities": {action: (0.9 if action == choice else 0.1 / (len(actions) - 1))
                                          for action in actions}}}


class FakeGateway:
    def __init__(self):
        self.calls = []
        self.error = None
        self.output = choice_output()
        self.model_id = MODELS[Service.DECISIONS]
        self.release = None

    async def judge(self, **kwargs):
        self.calls.append((Service.DECISIONS, kwargs))
        if self.release:
            await self.release.wait()
        if self.error:
            raise self.error
        return SimpleNamespace(output=deepcopy(self.output), model_id=self.model_id, request_id=str(uuid4()))


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
    assert result["max_steps"] == 60 and result["model_id"] == "gemini-decision-1"
    assert result["request_id"]
    assert result["decision"] == {"action": "accelerate", "target_speed_mps": 6.5,
                                  "reason": "Decisions selected accelerate; target 6.50 m/s.", "confidence": 0.9}
    assert len(gateway.calls) == 1
    service, call = gateway.calls[0]
    assert service == Service.DECISIONS and call["stage"] == "simulator_driver"
    assert call["questions"]["maneuver"]["type"] == "choice"
    assert set(call["questions"]["maneuver"]["criteria"]) == {"accelerate", "brake", "cruise", "stop"}
    context = call["context"]
    assert context.purpose == "development" and context.source_id is None and context.capture_id is None
    assert context.episode_id == "sim-driver:test-run-0001"
    assert context.prompt_id == "sim-driver-decisions-v3:step-0"
    assert "synthetic traffic simulator" in call["input_value"]
    assert json.loads(call["input_value"].split("Observation JSON:\n")[1]) == {
        **body["observation"], "go_around_available": False, "go_around_side": None, "go_around_active": False}


@pytest.mark.parametrize("path,value", [
    (("step",), -1), (("step",), MAX_STEPS), (("step",), True), (("step",), "0"),
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
    (("observation", "go_around_available"), 1), (("observation", "go_around_available"), "true"),
    (("observation", "go_around_available"), None), (("observation", "go_around_active"), 0),
    (("observation", "go_around_active"), "false"), (("observation", "go_around_active"), None),
    (("observation", "go_around_side"), True), (("observation", "go_around_side"), "up"),
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


def test_duplicate_idempotency_and_sixty_step_limit():
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
    {"type": "score"}, {"choice": "teleport"}, {"choice": []},
    {"confidence": 1.01}, {"confidence": float("nan")}, {"confidence": True},
    {"confidence": "0.9"}, {"probabilities": None}, {"probabilities": {}},
    {"probabilities": {"stop": 0.0, "brake": 0.0, "cruise": 0.0, "accelerate": 1.1}},
    {"probabilities": {"stop": 0.0, "brake": 0.0, "cruise": 0.0, "accelerate": float("nan")}},
    {"probabilities": {"stop": 0.0, "brake": 0.0, "cruise": 0.0, "accelerate": True}},
    {"probabilities": {"stop": 0.9, "brake": 0.03, "cruise": 0.04, "accelerate": 0.03}},
])
def test_invalid_provider_decisions_fail_without_a_fake_fallback(change):
    gateway = FakeGateway()
    gateway.output["maneuver"].update(change)
    client, _, _ = client_for(gateway)
    response = client.post("/api/driver/decision", json=request_body())
    assert response.status_code == 502 and response.json()["detail"]["code"] == "invalid_output"
    assert "decision" not in response.json()
    assert client.post("/api/driver/decision", json=request_body()).json() == response.json()
    assert len(gateway.calls) == 1


@pytest.mark.parametrize("output", [None, [], {}, {"other": {}}, {"maneuver": None}])
def test_missing_or_unexpected_answers_fail_closed(output):
    gateway = FakeGateway()
    gateway.output = output
    client, _, _ = client_for(gateway)
    response = client.post("/api/driver/decision", json=request_body())
    assert response.status_code == 502 and response.json()["detail"]["code"] == "invalid_output"


@pytest.mark.parametrize("action,speed,limit,expected", [
    ("stop", 4.0, 11.176, 0.0), ("brake", 4.0, 11.176, 1.5),
    ("brake", 1.0, 11.176, 0.0), ("brake", 50.0, 5.0, 5.0),
    ("cruise", 4.0, 11.176, 4.0), ("cruise", 20.0, 5.0, 5.0),
    ("accelerate", 4.0, 5.0, 5.0), ("accelerate", 10.0, 50.0, 11.176),
    ("accelerate", 0.0, 11.176, 2.5),
])
def test_choice_maps_to_bounded_target(action, speed, limit, expected):
    gateway = FakeGateway()
    gateway.output = choice_output(action)
    client, _, _ = client_for(gateway)
    body = request_body()
    body["observation"].update(speed_mps=speed, speed_limit_mps=limit)
    response = client.post("/api/driver/decision", json=body)
    assert response.status_code == 200
    decision = response.json()["decision"]
    assert decision["action"] == action and decision["target_speed_mps"] == expected
    assert decision["target_speed_mps"] <= min(limit, 11.176)
    if action == "brake":
        assert decision["target_speed_mps"] <= speed


@pytest.mark.parametrize("flags", [
    {"go_around_available": True}, {"go_around_active": True},
    {"go_around_available": True, "go_around_active": True},
    {"go_around_side": "left"}, {"go_around_side": "right"},
])
def test_incoherent_go_around_capabilities_reject_before_gateway(flags):
    client, gateway, _ = client_for()
    body = request_body()
    body["observation"].update(flags)
    response = client.post("/api/driver/decision", json=body)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_input"
    assert not gateway.calls


@pytest.mark.parametrize("side,active,limit,expected", [
    ("left", False, 11.176, 4.0), ("right", False, 2.5, 2.5),
    ("left", True, 11.176, 4.0), ("right", True, 50.0, 4.0),
])
def test_go_around_choice_uses_only_available_corridor_with_bounded_speed(side, active, limit, expected):
    gateway = FakeGateway()
    gateway.output = choice_output("go_around", go_around=True)
    client, _, _ = client_for(gateway)
    body = request_body()
    body["observation"].update(go_around_available=True, go_around_active=active, go_around_side=side,
                               speed_limit_mps=limit, lead_gap_m=25.0, lead_speed_mps=0.0)
    response = client.post("/api/driver/decision", json=body)
    assert response.status_code == 200
    decision = response.json()["decision"]
    assert decision == {"action": "go_around", "target_speed_mps": expected, "confidence": 0.9,
                        "reason": f"Decisions selected go around on the {side}; target {expected:.2f} m/s."}
    question = gateway.calls[0][1]["questions"]["maneuver"]
    assert set(question["criteria"]) == {"stop", "brake", "cruise", "accelerate", "go_around"}
    assert f"clear {side} corridor" in question["criteria"]["go_around"]
    assert "instead of waiting indefinitely" in question["instructions"]
    assert client.post("/api/driver/decision", json=body).json() == response.json()
    assert len(gateway.calls) == 1
    body["observation"]["go_around_side"] = "right" if side == "left" else "left"
    assert client.post("/api/driver/decision", json=body).status_code == 409


@pytest.mark.parametrize("updates", [
    {}, {"go_around_active": True, "go_around_side": "right"},
    {"go_around_available": True, "go_around_side": "left", "lead_gap_m": None, "lead_speed_mps": None},
    {"go_around_available": True, "go_around_side": "left", "lead_gap_m": 25.0, "lead_speed_mps": 1.0},
    {"go_around_available": True, "go_around_side": "left", "signal": "red", "stop_distance_m": 20.0},
    {"go_around_available": True, "go_around_side": "left", "signal": "amber", "stop_distance_m": 3.0},
    {"go_around_available": True, "go_around_active": True, "go_around_side": "right",
     "signal": "red", "stop_distance_m": 2.0},
    {"go_around_available": True, "go_around_side": "left", "remaining_m": 0.0},
])
def test_unavailable_go_around_is_not_offered_and_cannot_be_selected(updates):
    client, gateway, _ = client_for()
    body = request_body()
    body["observation"].update(lead_gap_m=25.0, lead_speed_mps=0.0)
    body["observation"].update(updates)
    gateway.output = choice_output("stop")
    response = client.post("/api/driver/decision", json=body)
    assert response.status_code == 200
    assert "go_around" not in gateway.calls[0][1]["questions"]["maneuver"]["criteria"]
    gateway.output = choice_output("go_around", go_around=True)
    body["step"] = 1
    response = client.post("/api/driver/decision", json=body)
    assert response.status_code == 502 and response.json()["detail"]["code"] == "invalid_output"
    assert "decision" not in response.json()


def test_active_clear_corridor_can_finish_after_the_obstruction_is_behind():
    client, gateway, _ = client_for()
    body = request_body()
    body["observation"].update(go_around_available=True, go_around_active=True, go_around_side="right")
    # No current lead is expected while passing or rejoining; local geometry
    # validates the retained plan rather than inventing a new obstacle here.
    gateway.output = choice_output("go_around", go_around=True)
    assert client.post("/api/driver/decision", json=body).status_code == 200


def test_legacy_observations_and_explicit_disabled_capability_share_idempotency():
    client, gateway, _ = client_for()
    body = request_body()
    first = client.post("/api/driver/decision", json=body)
    body["observation"].update(go_around_available=False, go_around_active=False, go_around_side=None)
    assert client.post("/api/driver/decision", json=body).json() == first.json()
    assert len(gateway.calls) == 1


def test_wrong_provider_model_is_rejected():
    gateway = FakeGateway()
    gateway.model_id = MODELS[Service.HYDROGEN]
    client, _, _ = client_for(gateway)
    response = client.post("/api/driver/decision", json=request_body())
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


@pytest.mark.parametrize("go_around", [False, True])
def test_driver_calls_real_decisions_adapter_and_retains_ledger_provenance(tmp_path, go_around):
    requests = []

    def handler(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert request.url.path == "/v1beta/decisions"
        assert payload["model"] == "gemini-decision-1"
        assert set(payload) == {"model", "input", "questions"}
        assert payload["questions"]["maneuver"]["type"] == "choice"
        assert "target 6.500 m/s" in payload["questions"]["maneuver"]["criteria"]["accelerate"]
        assert ("go_around" in payload["questions"]["maneuver"]["criteria"]) == go_around
        if go_around:
            assert "target 4.000 m/s" in payload["questions"]["maneuver"]["criteria"]["go_around"]
        return httpx.Response(200, json={"answers": choice_output("go_around" if go_around else "accelerate", go_around=go_around), "usage": {
            "total_input_tokens": 200, "total_output_tokens": 0, "total_tokens": 200}})

    async def scenario():
        config = GatewayConfig(data_dir=tmp_path / "ledger", rates_valid_through=date(2030, 1, 1))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            gateway = ModelGateway(config, http, api_key="test-driver-key")
            controller = DriverController(gateway)
            payload = request_body()
            if go_around:
                payload["observation"].update(go_around_available=True, go_around_side="right",
                                              lead_gap_m=25.0, lead_speed_mps=0.0)
            body = DriverRequest.model_validate(payload)
            result = await controller.decide(body)
            assert result.decision.target_speed_mps == (4.0 if go_around else 6.5)
            assert result.decision.action == ("go_around" if go_around else "accelerate")
            assert await controller.decide(body) == result
            assert len(requests) == 1
            service = next(s for s in gateway.status()["services"] if s["service"] == "decisions")
            assert service["state"] == "connected" and service["last_request_id"] == result.request_id
            assert "simulator_driver" in gateway.status()["budgets"]["stages"]
    asyncio.run(scenario())


@pytest.mark.parametrize("choice", [[], {}, None])
def test_malformed_choice_finalizes_real_gateway_attempt_and_allows_next_call(tmp_path, choice):
    requests = []

    def handler(request):
        requests.append(request)
        answers = choice_output()
        if len(requests) == 1:
            answers["maneuver"]["choice"] = choice
        return httpx.Response(200, json={"answers": answers, "usage": {
            "total_input_tokens": 200, "total_output_tokens": 0, "total_tokens": 200}})

    async def scenario():
        config = GatewayConfig(data_dir=tmp_path / "ledger", rates_valid_through=date(2030, 1, 1),
                               max_concurrency=1)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            gateway = ModelGateway(config, http, api_key="test-driver-key")
            controller = DriverController(gateway)
            body = DriverRequest.model_validate(request_body())
            with pytest.raises(HTTPException) as failure:
                await controller.decide(body)
            assert failure.value.status_code == 502
            assert failure.value.detail["code"] == "invalid_output"
            record, = gateway.ledger.attempts()
            assert failure.value.detail["request_id"] == record["request_id"]
            assert record["outcome"] == "invalid_output" and record["finished_at"] is not None
            assert record["dispatched"] == 1 and record["charged_tokens"] == 200
            assert json.loads(record["usage"])["total_tokens"] == 200
            assert gateway.slots._value == 1 and controller.in_flight == 0
            with pytest.raises(HTTPException) as duplicate:
                await controller.decide(body)
            assert duplicate.value.detail == failure.value.detail and len(requests) == 1
            result = await controller.decide(DriverRequest.model_validate(request_body(1)))
            assert result.decision.action == "accelerate" and len(requests) == 2
            assert all(row["outcome"] != "pending" for row in gateway.ledger.attempts())
    asyncio.run(scenario())


def test_driver_timeout_cancels_gateway_releases_slots_and_caches_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.driver.DRIVER_TIMEOUT_SECONDS", 0.02)

    async def scenario():
        calls = []
        release = asyncio.Event()

        async def handler(request):
            calls.append(request)
            await release.wait()
            return httpx.Response(200, json={"answers": choice_output()})

        config = GatewayConfig(data_dir=tmp_path / "ledger", rates_valid_through=date(2030, 1, 1))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            gateway = ModelGateway(config, http, api_key="test-driver-key")
            controller = DriverController(gateway)
            body = DriverRequest.model_validate(request_body())
            with pytest.raises(HTTPException) as failure:
                await controller.decide(body)
            assert failure.value.status_code == 504 and failure.value.detail["code"] == "timeout"
            assert controller.in_flight == 0 and not controller.runs[body.run_id].in_flight
            assert gateway.slots._value == config.max_concurrency
            service = next(s for s in gateway.status()["services"] if s["service"] == "decisions")
            assert service["reason"] == "cancelled"
            assert failure.value.detail["request_id"] == service["last_request_id"]
            with pytest.raises(HTTPException) as duplicate:
                await controller.decide(body)
            assert duplicate.value.detail == failure.value.detail and len(calls) == 1
            release.set()
            assert (await controller.decide(DriverRequest.model_validate(request_body(1)))).step == 1
            assert len(calls) == 2
    asyncio.run(scenario())


def test_existing_explicit_stage_config_inherits_navigation_cap_without_mutation():
    budget = StageBudget(tokens=500, usd="0.01")
    legacy = {stage: budget for stage in STAGES if stage != "simulator_driver"}
    config = GatewayConfig(stages=legacy)
    assert config.stages["simulator_driver"] == budget
    assert "simulator_driver" not in legacy
    explicit = {**legacy, "simulator_driver": StageBudget(tokens=100, usd="0.005")}
    assert GatewayConfig(stages=explicit).stages["simulator_driver"] == explicit["simulator_driver"]
    assert STAGES["navigation"] == Service.HYDROGEN
