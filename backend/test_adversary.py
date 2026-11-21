"""Adversary budget, validation and transport tests; never use a provider or credentials."""
import asyncio
from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from backend.adversary import (
    AdversaryController, AdversaryDecision, AttackRequest, MAX_CALLS, MAX_TRIGGER_SECONDS,
    REACTION_SECONDS, RUN_SECONDS, min_avoidable_distance, planning_speed, router,
)
from backend.driver import MAX_REQUEST_BYTES
from backend.gateway import GatewayConfig, ModelGateway
from backend.gateway.contracts import ErrorCode, GatewayError, Service

URL = "/api/adversary/attack"
ATTACK = {"action": "attack", "event_type": "car-crash", "trigger_seconds": 8.0,
          "parameters": {"distanceAheadM": 80.0, "approachSeconds": 2.0}, "durationSeconds": 8.0,
          "reason": "The car is cruising; a crash ahead tests braking."}
WAIT = {"action": "wait", "event_type": None, "trigger_seconds": None, "parameters": None,
        "durationSeconds": None, "reason": "The car is still accelerating."}


def request_body(attack_index=0, previous=(), time=5.0, speed=5.0, run_id="test-run-0001"):
    return {"run_id": run_id, "attack_index": attack_index,
            "state": {"simulation_time": time, "speed_mps": speed, "remaining_m": 400.0},
            "previous_attacks": [dict(item) for item in previous]}


class FakeGateway:
    def __init__(self, output=ATTACK):
        self.calls = []
        self.error = None
        self.output = deepcopy(output)
        self.release = None

    async def generate(self, service, **kwargs):
        self.calls.append((service, kwargs))
        if self.release:
            await self.release.wait()
        if self.error:
            raise self.error
        return SimpleNamespace(output=deepcopy(self.output), model_id="gemini-4-carbon", request_id=str(uuid4()))


def client_for(gateway=None):
    gateway = gateway or FakeGateway()
    app = FastAPI()
    app.state.adversary = AdversaryController(gateway)
    app.include_router(router)
    return TestClient(app), gateway, app.state.adversary


def detail(response):
    return response.json()["detail"]


def test_valid_attack_uses_carbon_adversary_stage_and_data_only_prompt():
    client, gateway, _ = client_for()
    response = client.post(URL, json=request_body())
    assert response.status_code == 200
    result = response.json()
    assert result["run_id"] == "test-run-0001" and result["attack_index"] == 0
    assert result["model_id"] == "gemini-4-carbon" and result["max_attacks"] == 2
    assert result["request_id"] and result["decision"] == ATTACK
    service, call = gateway.calls[0]
    assert service == Service.CARBON and call["stage"] == "adversary"
    assert call["response_model"] is AdversaryDecision
    assert call["context"].episode_id == "sim-adversary:test-run-0001"
    assert call["context"].prompt_id == "sim-adversary-v1:call-0" and call["context"].purpose == "development"
    data = json.loads(call["input_value"].split("Input JSON:\n")[1])
    assert data["state"] == request_body()["state"] and data["previous_attacks"] == []
    assert data["budget"]["max_trigger_seconds"] == MAX_TRIGGER_SECONDS == RUN_SECONDS - 12 == 48
    assert data["budget"]["adversary_interval_seconds"] == 3
    assert set(data["menu"]["event_types"]) == {"car-crash", "stalled-vehicle", "cut-in-vehicle", "hard-braking", "slow-traffic", "two-car-roadblock"}


@pytest.mark.parametrize("event_type", ["cut-in-vehicle", "hard-braking", "slow-traffic", "two-car-roadblock"])
def test_new_hazards_are_accepted_and_preserve_model_choice(event_type):
    choice = dict(ATTACK, event_type=event_type)
    client, gateway, _ = client_for(FakeGateway(choice))
    response = client.post(URL, json=request_body())
    assert response.status_code == 200 and response.json()["decision"] == choice
    prompt = json.loads(gateway.calls[0][1]["input_value"].split("Input JSON:\n")[1])
    assert event_type in prompt["menu"]["event_types"]


def test_valid_wait_leaves_slot_open_and_identical_replay_is_cached():
    client, gateway, _ = client_for(FakeGateway(WAIT))
    first = client.post(URL, json=request_body())
    assert first.status_code == 200 and first.json()["decision"] == WAIT
    assert client.post(URL, json=request_body()).json() == first.json() and len(gateway.calls) == 1
    gateway.output = deepcopy(ATTACK)
    later = client.post(URL, json=request_body(time=7.0))
    assert later.status_code == 200 and later.json()["decision"]["action"] == "attack"
    assert len(gateway.calls) == 2


def test_second_attack_reason_is_never_fed_back():
    client, gateway, _ = client_for()
    client.post(URL, json=request_body())
    gateway.output = dict(ATTACK, trigger_seconds=19.0, reason="second")
    prior = [{"event_type": "car-crash", "trigger_seconds": 8.0}]
    response = client.post(URL, json=request_body(1, prior, time=9.0))
    assert response.status_code == 200 and response.json()["attack_index"] == 1
    prompt = gateway.calls[1][1]["input_value"]
    assert ATTACK["reason"] not in prompt
    assert json.loads(prompt.split("Input JSON:\n")[1])["previous_attacks"] == prior


@pytest.mark.parametrize("body", [
    request_body(2, [{"event_type": "car-crash", "trigger_seconds": 3.0},
                     {"event_type": "stalled-vehicle", "trigger_seconds": 14.0}]),
    request_body(1, [{"event_type": "car-crash", "trigger_seconds": 3.0},
                     {"event_type": "stalled-vehicle", "trigger_seconds": 14.0}]),
    request_body(5),
    request_body(1),  # index disagrees with previous_attacks
    request_body(0, [{"event_type": "car-crash", "trigger_seconds": 3.0}]),
    request_body(1, [{"event_type": "car-crash", "trigger_seconds": 3.0}]),  # ledger issued nothing
])
def test_budget_refused_without_gateway_call(body):
    client, gateway, _ = client_for()
    response = client.post(URL, json=body)
    assert response.status_code == 409 and detail(response)["code"] == "invalid_input"
    assert not gateway.calls


def test_ledger_not_request_decides_budget():
    client, gateway, _ = client_for()
    client.post(URL, json=request_body())
    wrong = [{"event_type": "stalled-vehicle", "trigger_seconds": 8.0}]
    assert client.post(URL, json=request_body(1, wrong, time=9.0)).status_code == 409
    assert len(gateway.calls) == 1


def test_slot_replay_returns_stored_attack_even_with_new_state():
    client, gateway, _ = client_for()
    first = client.post(URL, json=request_body())
    gateway.output = dict(ATTACK, event_type="stalled-vehicle")
    assert client.post(URL, json=request_body(time=6.0)).json() == first.json()
    assert len(gateway.calls) == 1


def test_wait_calls_are_capped():
    client, gateway, _ = client_for(FakeGateway(WAIT))
    for call in range(MAX_CALLS):
        assert client.post(URL, json=request_body(time=float(call))).status_code == 200
    assert client.post(URL, json=request_body(time=20.0)).status_code == 409
    assert len(gateway.calls) == MAX_CALLS


def test_concurrent_same_slot_gets_one_call_and_one_result():
    async def scenario():
        gateway = FakeGateway()
        gateway.release = asyncio.Event()
        controller = AdversaryController(gateway)
        one = AttackRequest.model_validate(request_body())
        two = AttackRequest.model_validate(request_body(time=6.0))
        tasks = [asyncio.create_task(controller.attack(one)), asyncio.create_task(controller.attack(two))]
        await asyncio.sleep(0)
        gateway.release.set()
        first, second = await asyncio.gather(*tasks)
        assert first == second and len(gateway.calls) == 1 and controller.in_flight == 0
    asyncio.run(scenario())


def test_cancellation_stores_failure_and_prevents_duplicate_bill():
    async def scenario():
        gateway = FakeGateway()
        gateway.release = asyncio.Event()
        controller = AdversaryController(gateway)
        body = AttackRequest.model_validate(request_body())
        task = asyncio.create_task(controller.attack(body))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert controller.in_flight == 0
        with pytest.raises(HTTPException) as exc:
            await controller.attack(body)
        assert exc.value.detail["code"] == "cancelled" and len(gateway.calls) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("dispatched", [False, True])
def test_cancelled_real_gateway_retains_only_actual_attempt_id(tmp_path, dispatched):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(request):
            calls.append(request)
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"status": "completed", "steps": [{"type": "model_output",
                "content": [{"type": "text", "text": json.dumps(ATTACK)}]}]})

        config = GatewayConfig(data_dir=tmp_path / "ledger", rates_valid_through=date(2030, 1, 1),
                               max_concurrency=1)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            gateway = ModelGateway(config, http, api_key="test-adversary-key")
            controller = AdversaryController(gateway)
            body = AttackRequest.model_validate(request_body())
            if not dispatched:
                await gateway.slots.acquire()
            task = asyncio.create_task(controller.attack(body))
            if dispatched:
                await entered.wait()
            else:
                await asyncio.sleep(0)
            task.cancel("private cancellation detail")
            with pytest.raises(asyncio.CancelledError) as cancelled:
                await task
            if not dispatched:
                gateway.slots.release()
            records = gateway.ledger.attempts()
            expected_id = records[0]["request_id"] if dispatched else None
            assert getattr(cancelled.value, "request_id", None) == expected_id
            assert cancelled.value.args == ("private cancellation detail",)
            assert len(records) == int(dispatched) and len(calls) == int(dispatched)
            if dispatched:
                assert records[0]["outcome"] == "cancelled" and records[0]["usage"] is None
                assert records[0]["charged_tokens"] > 0
            with pytest.raises(HTTPException) as duplicate:
                await controller.attack(body)
            assert duplicate.value.detail["code"] == "cancelled"
            assert duplicate.value.detail["request_id"] == expected_id
            assert "private cancellation detail" not in json.dumps(duplicate.value.detail)
            assert len(calls) == int(dispatched) and controller.runs[body.run_id].calls == 1
            assert controller.in_flight == 0 and gateway.slots._value == 1
            release.set()
            next_body = AttackRequest.model_validate(request_body(run_id="next-run-0002"))
            assert (await controller.attack(next_body)).decision.action == "attack"
            assert len(calls) == int(dispatched) + 1
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    {"extra": "x"}, {"action": "teleport"}, {"event_type": "pedestrian"},
    {"trigger_seconds": MAX_TRIGGER_SECONDS + 0.5}, {"trigger_seconds": -1.0}, {"trigger_seconds": "8"},
    {"trigger_seconds": True}, {"trigger_seconds": float("nan")},
    {"parameters": {"distanceAheadM": 101.0, "approachSeconds": 2.0}},
    {"parameters": {"distanceAheadM": 19.0, "approachSeconds": 2.0}},
    {"parameters": {"distanceAheadM": 40.0, "approachSeconds": 4.5}},
    {"parameters": {"distanceAheadM": 40.0, "approachSeconds": 2.0, "extra": 1}},
    {"parameters": None}, {"durationSeconds": 0.5}, {"durationSeconds": 21.0},
    {"reason": ""}, {"reason": "x" * 241},
    {"trigger_seconds": 4.0},  # before state.simulation_time
])
def test_invalid_attack_output_is_rejected_without_fallback(change):
    gateway = FakeGateway(dict(ATTACK, **change))
    client, _, _ = client_for(gateway)
    response = client.post(URL, json=request_body())
    assert response.status_code == 502 and detail(response)["code"] == "invalid_output"
    assert "decision" not in response.json()
    assert client.post(URL, json=request_body()).json() == response.json() and len(gateway.calls) == 1


@pytest.mark.parametrize("change", [
    {"event_type": "car-crash"}, {"trigger_seconds": 8.0}, {"durationSeconds": 5.0},
    {"parameters": {"distanceAheadM": 40.0, "approachSeconds": 2.0}},
])
def test_wait_with_any_attack_field_is_invalid(change):
    client, _, _ = client_for(FakeGateway(dict(WAIT, **change)))
    response = client.post(URL, json=request_body())
    assert response.status_code == 502 and detail(response)["code"] == "invalid_output"


def test_trigger_at_cutoff_late_in_long_run_is_accepted():
    client, _, _ = client_for(FakeGateway(dict(ATTACK, trigger_seconds=float(MAX_TRIGGER_SECONDS))))
    response = client.post(URL, json=request_body(time=45.0))
    assert response.status_code == 200 and response.json()["decision"]["trigger_seconds"] == 48


@pytest.mark.parametrize("trigger", [17.9, 12.0])
def test_trigger_too_close_to_previous_attack_is_rejected(trigger):
    client, gateway, _ = client_for()
    client.post(URL, json=request_body())
    gateway.output = dict(ATTACK, trigger_seconds=trigger)
    response = client.post(URL, json=request_body(1, [{"event_type": "car-crash", "trigger_seconds": 8.0}], time=9.0))
    assert response.status_code == 502 and detail(response)["code"] == "invalid_output"


@pytest.mark.parametrize("first,second,accepted", [
    (3.1, 13.1, False),  # requested gap 10 s, actual barriers 6 -> 15 only 9 s
    (3.1, 15.0, False), (3.1, 15.1, True), (3.0, 13.0, True),
    (5.0, 15.0 + 5e-10, False),  # within the injector's 1e-9 boundary tolerance
    (5.0, 15.1, True), (37.1, 48.0, False), (36.0, 48.0, True),
])
def test_requested_and_applied_attack_times_both_keep_minimum_gap(first, second, accepted):
    gateway = FakeGateway(dict(ATTACK, trigger_seconds=first))
    client, _, _ = client_for(gateway)
    assert client.post(URL, json=request_body(time=0.0)).status_code == 200
    gateway.output = dict(ATTACK, trigger_seconds=second)
    prior = [{"event_type": "car-crash", "trigger_seconds": first}]
    response = client.post(URL, json=request_body(1, prior, time=first + 1))
    assert response.status_code == (200 if accepted else 502)
    if accepted:
        assert response.json()["decision"]["trigger_seconds"] == second
    else:
        assert detail(response)["code"] == "invalid_output"
        assert client.post(URL, json=request_body(1, prior, time=first + 1)).json() == response.json()
        assert len(gateway.calls) == 2


def test_unavoidable_attack_is_a_typed_error_not_clamped():
    speed = 10.0
    limit = min_avoidable_distance(speed)
    assert REACTION_SECONDS == 3 and limit == pytest.approx(10 * REACTION_SECONDS + 100 / 7 + 5)
    client, _, _ = client_for(FakeGateway(dict(ATTACK, trigger_seconds=6.0, parameters={"distanceAheadM": limit - 0.1, "approachSeconds": 2.0})))
    response = client.post(URL, json=request_body(speed=speed,time=6.0))
    assert response.status_code == 502 and detail(response)["code"] == "unavoidable_attack"
    assert "decision" not in response.json()
    client, _, _ = client_for(FakeGateway(dict(ATTACK, trigger_seconds=6.0, parameters={"distanceAheadM": limit + 0.1, "approachSeconds": 2.0})))
    assert client.post(URL, json=request_body(speed=speed,time=6.0)).status_code == 200


def test_model_may_use_the_exact_minimum_distance_advertised_in_its_prompt():
    class EchoMinimumGateway(FakeGateway):
        async def generate(self, service, **kwargs):
            data = json.loads(kwargs["input_value"].split("Input JSON:\n")[1])
            self.output["parameters"]["distanceAheadM"] = data["budget"]["min_distance_ahead_m"]
            return await super().generate(service, **kwargs)

    client, gateway, _ = client_for(EchoMinimumGateway())
    response = client.post(URL, json=request_body(speed=8.0))
    assert response.status_code == 200
    distance = response.json()["decision"]["parameters"]["distanceAheadM"]
    assert distance >= min_avoidable_distance(8.0)
    assert distance == gateway.output["parameters"]["distanceAheadM"]


def test_future_hazard_plans_for_acceleration_without_relabeling_observed_speed():
    body = AttackRequest.model_validate(request_body(time=0.0, speed=0.0))
    data = json.loads(AdversaryController.prompt(body).split("Input JSON:\n")[1])
    assert data["state"]["speed_mps"] == 0.0
    assert data["budget"]["planning_speed_mps"] == 11.176
    assert data["budget"]["min_distance_ahead_m"] == 56.38
    assert data["budget"]["min_distance_ahead_m"] >= min_avoidable_distance(11.176)
    assert planning_speed(body.state, 0) == 0
    # Trigger 3.1 is injected at 6 s, including the acceleration to that barrier.
    assert planning_speed(body.state, 3.1) == 9


@pytest.mark.parametrize("distance,accepted", [(25.0, False), (21.0, False), (56.38, True)])
def test_future_attack_is_validated_for_its_trigger_speed(distance, accepted):
    choice = dict(ATTACK, trigger_seconds=12.0, parameters={"distanceAheadM": distance, "approachSeconds": 1.5})
    client, gateway, _ = client_for(FakeGateway(choice))
    response = client.post(URL, json=request_body(time=0.0, speed=0.0))
    if accepted:
        assert response.status_code == 200 and response.json()["decision"] == choice
    else:
        assert response.status_code == 502 and detail(response)["code"] == "unavoidable_attack"
        assert client.post(URL, json=request_body(time=0.0, speed=0.0)).json() == response.json()
    assert len(gateway.calls) == 1


@pytest.mark.parametrize("path,value", [
    (("extra",), "x"), (("state", "extra"), "x"), (("run_id",), "short"), (("run_id",), "../a/b/c"),
    (("attack_index",), True), (("attack_index",), "0"), (("attack_index",), -1),
    (("state", "speed_mps"), True), (("state", "speed_mps"), "5"), (("state", "speed_mps"), 51),
    (("state", "simulation_time"), RUN_SECONDS + 0.5),
    (("previous_attacks",), [{"event_type": "car-crash", "trigger_seconds": MAX_TRIGGER_SECONDS + 0.5}]), (("state", "remaining_m"), 100001),
    (("previous_attacks",), [{"event_type": "car-crash", "trigger_seconds": 3.0, "reason": "x"}]),
    (("previous_attacks",), [{"event_type": "car-crash", "trigger_seconds": True}]),
    (("previous_attacks",), [{"event_type": "pedestrian", "trigger_seconds": 3.0}]),
    (("previous_attacks",), [{"event_type": "car-crash", "trigger_seconds": 3.0}] * 3),
])
def test_request_strictness_rejects_before_gateway(path, value):
    client, gateway, _ = client_for()
    body = request_body()
    target = body if len(path) == 1 else body[path[0]]
    target[path[-1]] = value
    response = client.post(URL, json=body)
    assert response.status_code == 422 and detail(response)["code"] == "invalid_input"
    assert not gateway.calls


@pytest.mark.parametrize("raw", [
    "{", "[]", '{"attack_index":0,"attack_index":1}',
    json.dumps(request_body()).replace('"speed_mps": 5.0', '"speed_mps": NaN'),
    json.dumps(request_body()).replace('"speed_mps": 5.0', '"speed_mps": Infinity'),
    json.dumps(request_body()).replace('"speed_mps": 5.0', '"speed_mps": 1e999'),
])
def test_invalid_json_and_nonfinite_numbers(raw):
    client, gateway, _ = client_for()
    response = client.post(URL, content=raw, headers={"content-type": "application/json"})
    assert response.status_code == 422 and not gateway.calls


def test_oversized_and_wrong_content_type_bodies():
    client, gateway, _ = client_for()
    data = b" " * (MAX_REQUEST_BYTES + 1)
    assert client.post(URL, content=data, headers={"content-type": "application/json"}).status_code == 413
    assert client.post(URL, content="{}").status_code == 415
    assert not gateway.calls


@pytest.mark.parametrize("error,status,code", [
    (GatewayError(ErrorCode.NOT_CONFIGURED), 503, "not_configured"),
    (GatewayError(ErrorCode.BUDGET), 429, "budget_exceeded"),
    (GatewayError(ErrorCode.TIMEOUT, retryable=True), 504, "timeout"),
    (RuntimeError("upstream leaked secret-test-credential"), 502, "upstream_error"),
])
def test_provider_failure_is_redacted_stored_and_never_falls_back(error, status, code):
    gateway = FakeGateway()
    gateway.error = error
    client, _, controller = client_for(gateway)
    response = client.post(URL, json=request_body())
    assert response.status_code == status and detail(response)["code"] == code
    assert "secret-test-credential" not in response.text and "decision" not in response.json()
    gateway.error = None
    assert client.post(URL, json=request_body(time=6.0)).json() == response.json()
    assert len(gateway.calls) == 1 and controller.in_flight == 0


def test_no_secret_or_provider_text_escapes():
    gateway = FakeGateway()
    gateway.error = GatewayError(ErrorCode.UPSTREAM, request_id="secret-test-credential")
    gateway.error.args = ("upstream leaked secret-test-credential",)
    client, _, _ = client_for(gateway)
    response = client.post(URL, json=request_body())
    assert response.status_code == 502 and "secret-test-credential" not in response.text
    assert detail(response)["request_id"] is None
    leaky = FakeGateway(dict(ATTACK, reason="ok", extra="secret-test-credential"))
    client, _, _ = client_for(leaky)
    response = client.post(URL, json=request_body())
    assert response.status_code == 502 and "secret-test-credential" not in response.text
