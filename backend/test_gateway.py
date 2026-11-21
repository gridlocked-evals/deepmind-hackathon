import asyncio
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, ValidationError

from backend.gateway import CallContext, GatewayConfig, GatewayError, ModelGateway, Service
from backend.gateway.config import StageBudget
from backend.gateway.contracts import ErrorCode, canonical, digest
from backend.gateway.smoke import fixture_image, run_smoke
from backend.main import app

KEY = "test-secret-never-record-this"
CONTEXT = CallContext(episode_id="episode-1", prompt_id="prompt-v1")
USAGE = {"total_input_tokens": 12, "total_output_tokens": 7, "total_thought_tokens": 3, "total_tokens": 22}


class Observation(BaseModel):
    count: int = Field(ge=0)


def settings(tmp_path, **changes):
    return GatewayConfig(data_dir=tmp_path / "evidence", rates_valid_through=date(2030, 1, 1),
                         retry_delay_seconds=0, **changes)


def response(text='{"count":2}', **changes):
    return {"id": "provider-1", "status": "completed", "usage": USAGE,
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}], **changes}


async def observe(gateway, **changes):
    return await gateway.generate(Service.HYDROGEN, stage="observation", input_value="Count the vehicles.",
                                  context=CONTEXT, response_model=Observation, **changes)


def test_structured_driver_thinking_policy_is_scoped_and_retains_output_cap(tmp_path):
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=response())
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            for prompt in ("sim-driver-v2:step-0", "visual-navigation-v1"):
                await gateway.generate(Service.HYDROGEN, stage="navigation", input_value="Fixture observation",
                    context=CallContext(episode_id="fixture", prompt_id=prompt), response_model=Observation)
    asyncio.run(run())
    assert sent[0]["generation_config"]["thinking_level"] == "low"
    assert "thinking_level" not in sent[1]["generation_config"]
    assert sent[0]["generation_config"]["max_output_tokens"] == settings(tmp_path).output_tokens_per_call


def test_success_records_exact_payload_usage_and_survives_restart(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response())
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            assert gateway.status()["services"][0]["state"] == "unverified"
            result = await observe(gateway)
            assert result.output == {"count": 2}
            assert result.input_hash == digest(requests[0].content)
            assert requests[0].headers["x-goog-api-key"] == KEY
            assert requests[0].url.path == "/v1beta/interactions"
            assert json.loads(requests[0].content)["store"] is False
            again = ModelGateway(settings(tmp_path), client, api_key=KEY)
            record = again.ledger.attempts()[0]
            assert record["provider_request_id"] == "provider-1"
            assert record["charged_tokens"] == 22
            assert record["charged_microusd"] == 93
            assert record["latency_ms"] >= 0
            assert again.status()["services"][0]["state"] == "connected"
            assert ModelGateway(settings(tmp_path), client, api_key="different-secret").status()["services"][0]["state"] == "unverified"
            with again.ledger.connection() as db:
                db.execute("UPDATE attempts SET finished_at=?", (time.time() - 1000,))
            assert again.status()["services"][0]["state"] == "stale"
    asyncio.run(run())
    for path in (tmp_path / "evidence").rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()


def test_retries_preserve_input_even_if_caller_mutates_it(tmp_path):
    payloads = []
    source = [{"type": "text", "text": "Original immutable frame"}]
    def handler(request):
        payloads.append(request.content)
        source[0]["text"] = "Changed camera frame"
        return httpx.Response(429, headers={"retry-after": "999"}) if len(payloads) == 1 else httpx.Response(200, json=response())
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            await gateway.generate(Service.HYDROGEN, stage="observation", input_value=source,
                                   context=CONTEXT, response_model=Observation)
            records = gateway.ledger.attempts()
            assert [r["outcome"] for r in records] == ["success", "quota_exceeded"]
            assert len({r["call_id"] for r in records}) == 1
            assert records[1]["charged_tokens"] > records[0]["charged_tokens"]
    asyncio.run(run())
    assert len(payloads) == 2 and payloads[0] == payloads[1]


@pytest.mark.parametrize("status,code,attempts", [(401, ErrorCode.AUTH, 1), (403, ErrorCode.AUTH, 1),
    (429, ErrorCode.QUOTA, 2), (503, ErrorCode.UPSTREAM, 2), (400, ErrorCode.INVALID_INPUT, 1)])
def test_errors_are_typed_bounded_and_redacted(tmp_path, status, code, attempts):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "private upstream error " + KEY}, headers={"x-request-id": KEY})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            with pytest.raises(GatewayError) as error:
                await observe(gateway)
            assert error.value.code == code and error.value.request_id
            assert KEY not in json.dumps(error.value.public())
            assert len(gateway.ledger.attempts()) == attempts
            assert gateway.status()["services"][0]["state"] == "unavailable"
    asyncio.run(run())
    assert len(calls) == attempts
    assert all(KEY.encode() not in p.read_bytes() for p in (tmp_path / "evidence").rglob("*") if p.is_file())


@pytest.mark.parametrize("payload", [response(text='{"count":-1}'), response(text="not JSON"),
    response(steps=None), response(status="requires_continuation"), response(steps=[]),
    response(steps=[{"type": "model_output", "content": "invalid"}])])
def test_invalid_outputs_preserve_evidence_without_retry(tmp_path, payload):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            with pytest.raises(GatewayError, match="validation") as error:
                await observe(gateway)
            assert error.value.code == ErrorCode.INVALID_OUTPUT
            record = gateway.ledger.attempts()[0]
            assert record["result_hash"] and record["usage"]
            assert len(gateway.ledger.attempts()) == 1
    asyncio.run(run())


def test_timeout_is_not_retried_and_reservation_survives(tmp_path):
    calls = []
    async def handler(request):
        calls.append(request)
        await asyncio.sleep(.1)
        return httpx.Response(200, json=response())
    async def run():
        config = settings(tmp_path, timeout_seconds=.01)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config, client, api_key=KEY)
            with pytest.raises(GatewayError) as error:
                await observe(gateway)
            assert error.value.code == ErrorCode.TIMEOUT
            again = ModelGateway(config, client, api_key=KEY)
            record = again.ledger.attempts()[0]
            assert record["outcome"] == "timeout" and record["charged_tokens"] == 36864
    asyncio.run(run())
    assert len(calls) == 1


def test_cancellation_releases_slot_but_retains_unknown_usage(tmp_path):
    async def run():
        entered = asyncio.Event()
        async def handler(_):
            entered.set()
            await asyncio.sleep(5)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            task = asyncio.create_task(observe(gateway))
            await entered.wait()
            task.cancel("caller cancellation")
            with pytest.raises(asyncio.CancelledError) as cancelled:
                await task
            record = gateway.ledger.attempts()[0]
            assert record["outcome"] == "cancelled" and record["charged_tokens"] > 0
            assert cancelled.value.args == ("caller cancellation",)
            assert cancelled.value.request_id == record["request_id"]
    asyncio.run(run())


def test_concurrency_is_atomic_across_gateway_instances(tmp_path):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def handler(_):
            entered.set()
            await release.wait()
            return httpx.Response(200, json=response())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            config = settings(tmp_path, max_concurrency=1)
            first, second = ModelGateway(config, client, api_key=KEY), ModelGateway(config, client, api_key=KEY)
            pending = asyncio.create_task(observe(first))
            await entered.wait()
            with pytest.raises(GatewayError) as error:
                await observe(second)
            assert error.value.code == ErrorCode.CONCURRENCY
            release.set()
            await pending
            await observe(second)
    asyncio.run(run())


def test_parallel_calls_queue_in_process_instead_of_failing(tmp_path):
    answers = {"visible": {"type": "noul", "noul": .9, "confidence": .8}}
    in_flight = peak = 0
    async def handler(_):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(.05)
        in_flight -= 1
        return httpx.Response(200, json={"answers": answers, "usage": {"total_input_tokens": 10, "total_output_tokens": 0, "total_tokens": 10}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path, max_concurrency=2), client, api_key=KEY)
            questions = {"visible": {"type": "noul", "instructions": "Is it visible?"}}
            results = await asyncio.gather(*(gateway.judge(stage="image_gate", input_value="Street", context=CONTEXT,
                                                           questions=questions) for _ in range(4)))
            assert [result.output for result in results] == [answers] * 4
            assert peak == 2
    asyncio.run(run())


def test_in_process_queue_has_a_deadline_and_no_spending(tmp_path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail('unexpected dispatch'))) as client:
            gateway = ModelGateway(settings(tmp_path, timeout_seconds=.01), client, api_key=KEY)
            gateway.slots = asyncio.Semaphore(0)
            with pytest.raises(GatewayError) as error:
                await observe(gateway)
            assert error.value.code == ErrorCode.CONCURRENCY
            row = gateway.ledger.get(error.value.request_id)
            assert row['charged_tokens'] == 0 and row['dispatched'] == 0
    asyncio.run(run())


def test_demo_reserve_and_stage_caps_block_before_network(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response())
    async def run():
        # A Hydrogen reservation is $0.079872; ordinary work gets only $0.05.
        config = settings(tmp_path, total_usd=Decimal(".10"), demo_reserve_usd=Decimal(".05"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config, client, api_key=KEY)
            with pytest.raises(GatewayError) as error:
                await observe(gateway)
            assert error.value.code == ErrorCode.BUDGET and not requests
            demo = CallContext(episode_id="demo", prompt_id="v1", source_id="camera", capture_id="capture", purpose="demo")
            await gateway.generate(Service.HYDROGEN, stage="observation", input_value="Count", context=demo, response_model=Observation)
            stages = dict(config.stages)
            stages["observation"] = StageBudget(tokens=1)
            limited = ModelGateway(config.model_copy(update={"stages": stages}), client, api_key=KEY)
            with pytest.raises(GatewayError) as error:
                await observe(limited)
            assert error.value.code == ErrorCode.BUDGET and len(requests) == 1
    asyncio.run(run())


def test_missing_usage_is_charged_and_overrun_latches_across_restart(tmp_path):
    responses = [response(usage=None), response(usage={"total_input_tokens": 100000, "total_output_tokens": 7, "total_tokens": 100007})]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=responses.pop(0)))) as client:
            config = settings(tmp_path)
            gateway = ModelGateway(config, client, api_key=KEY)
            first = await observe(gateway)
            assert first.accounting == "reservation_retained"
            assert gateway.ledger.attempts()[0]["charged_tokens"] == 36864
            await observe(gateway)
            again = ModelGateway(config, client, api_key=KEY)
            assert again.status()["budgets"]["blocks"]
            with pytest.raises(GatewayError) as error:
                await observe(again)
            assert error.value.code == ErrorCode.BUDGET
    asyncio.run(run())


def test_decisions_protocol_all_types_and_probability_validation(tmp_path):
    questions = {"visible": {"type": "noul", "instructions": "Is it visible?"},
                 "class": {"type": "choice", "instructions": "Which?", "criteria": {"car": "", "bus": ""}},
                 "severity": {"type": "score", "instructions": "Rate severity", "criteria": ["low", "medium", "high"]}}
    answers = {"visible": {"type": "noul", "noul": .9, "confidence": .8},
               "class": {"type": "choice", "choice": "car", "confidence": .8, "probabilities": {"car": .8, "bus": .2}},
               "severity": {"type": "score", "score": 1.1, "confidence": .7,
                            "probabilities": {"0": .1, "1": .7, "2": .2}, "legend": {"0": "low", "1": "medium", "2": "high"}}}
    def handler(request):
        assert request.url.path == "/v1beta/decisions"
        assert json.loads(request.content)["model"] == "gemini-decision-1"
        return httpx.Response(200, json={"answers": answers, "usage": {"total_input_tokens": 100, "total_output_tokens": 0, "total_tokens": 100}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            result = await gateway.judge(stage="image_gate", input_value="Street", context=CONTEXT, questions=questions)
            assert result.output == answers
            answers["class"]["choice"] = "bus"
            with pytest.raises(GatewayError) as error:
                await gateway.judge(stage="image_gate", input_value="Street", context=CONTEXT, questions=questions)
            assert error.value.code == ErrorCode.INVALID_OUTPUT
    asyncio.run(run())


def test_image_response_is_decoded_and_corruption_rejected(tmp_path):
    image = fixture_image()
    def handler(request):
        body = json.loads(request.content)
        assert body["response_format"]["aspect_ratio"] == "16:9"
        assert "delivery" not in body["response_format"]
        assert body["model"] == "gemini-nano-banana-2.1"
        return httpx.Response(200, json=response(steps=[{"type": "model_output", "content": [image]}]))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            result = await gateway.generate(Service.NANO_BANANA, stage="reference", input_value="Render street", context=CONTEXT)
            assert (result.output["width"], result.output["height"]) == (320, 180)
            image["data"] = "bWFsZm9ybWVk"
            with pytest.raises(GatewayError):
                await gateway.generate(Service.NANO_BANANA, stage="reference", input_value="Render street", context=CONTEXT)
    asyncio.run(run())


@pytest.mark.parametrize("bad", [[{"type": "image", "uri": "https://example.org/latest.jpg"}],
    [{"type": "image", "mime_type": "image/jpeg", "data": "bad"}], "", KEY])
def test_mutable_urls_bad_media_and_credentials_never_dispatch(tmp_path, bad):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("unexpected network request"))) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            with pytest.raises(GatewayError) as error:
                await gateway.generate(Service.HYDROGEN, stage="observation", input_value=bad, context=CONTEXT)
            assert error.value.code == ErrorCode.INVALID_INPUT
    asyncio.run(run())


def test_submission_requires_both_distinct_roles_for_same_capture(tmp_path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response()))) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            args = dict(episode_id="episode", source_id="camera", capture_id="capture")
            for purpose in ("smoke", "submission"):
                context = CallContext(**args, prompt_id="v1", purpose=purpose)
                await gateway.generate(Service.HYDROGEN, stage="observation", input_value="Count", context=context, response_model=Observation)
                with pytest.raises(GatewayError):
                    gateway.require_submission_models(**args)
                await gateway.generate(Service.CARBON, stage="compilation", input_value="Compile", context=context, response_model=Observation)
                if purpose == "smoke":
                    with pytest.raises(GatewayError):
                        gateway.require_submission_models(**args)
            assert gateway.require_submission_models(**args)["ready"]
            with pytest.raises(GatewayError):
                gateway.require_submission_models(**{**args, "capture_id": "different-capture"})
    asyncio.run(run())


class WorldSession:
    def __init__(self, fail=False, no_frames=False):
        self.sent, self.completed, self.fail, self.no_frames = [], False, fail, no_frames

    async def send(self, message):
        self.sent.append(message)

    async def complete(self):
        self.completed = True

    async def receive(self):
        if self.fail:
            raise RuntimeError("upstream secret " + KEY)
        if self.no_frames:
            await asyncio.sleep(10)
        yield {"event_type": "step.delta", "delta": {"type": "video", "data": fixture_image()["data"]}}
        assert self.completed
        yield {"event_type": "interaction.completed", "usage": USAGE}


@pytest.mark.parametrize("failure", [None, "connect", "stream", "timeout", "cancel", "create_timeout"])
def test_genie_real_protocol_cleanup_and_unresolved_session_block(tmp_path, failure):
    calls = []
    world = WorldSession(fail=failure in ("stream", "cancel"), no_frames=failure == "timeout")
    @asynccontextmanager
    async def connector(key, identifier, timeout):
        assert key == KEY and identifier == "world-1"
        if failure == "connect":
            raise RuntimeError("socket refused " + KEY)
        yield world
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/cancel"):
            assert request.url.path == "/v1beta/interactions/world-1/cancel"
            return httpx.Response(503) if failure == "cancel" else httpx.Response(200, json={"status": "cancelled", "id": "world-1"})
        if failure == "create_timeout":
            raise httpx.ReadTimeout("secret " + KEY)
        assert json.loads(request.content) == {"model": "genie-4"}
        return httpx.Response(200, json={"id": "world-1", "status": "in_progress"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            config = settings(tmp_path, genie_seconds=.2, genie_enabled=True)
            gateway = ModelGateway(config, client, api_key=KEY, genie_connector=connector)
            context = CallContext(episode_id="smoke", prompt_id="world-v1", purpose="smoke")
            if failure is None:
                result = await gateway.probe_world(prompt="A quiet street", context=context)
                assert result.output["termination"] == "interaction.completed"
                assert [e.get("action") for e in world.sent] == [None, "key_down", "key_up"]
                assert result.usage is not None
                assert len(calls) == 1
            else:
                with pytest.raises(GatewayError) as error:
                    await gateway.probe_world(prompt="A quiet street", context=context)
                assert KEY not in str(error.value)
                if failure in ("cancel", "create_timeout"):
                    assert error.value.code == ErrorCode.TERMINATION
                    count = len(calls)
                    again = ModelGateway(config, client, api_key=KEY, genie_connector=connector)
                    with pytest.raises(GatewayError) as second:
                        await again.probe_world(prompt="Try again", context=context)
                    assert second.value.code == ErrorCode.TERMINATION and len(calls) == count
                else:
                    assert len(calls) == 2 and calls[-1].endswith("/cancel")
    asyncio.run(run())


def test_second_genie_while_one_is_live_is_concurrency_not_termination(tmp_path):
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()
    class Blocking(WorldSession):
        async def receive(self):
            entered.set()
            await release.wait()
            async for event in super().receive():
                yield event
    world = Blocking()
    @asynccontextmanager
    async def connector(key, identifier, timeout):
        yield world
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"id": "world-1", "status": "in_progress"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path, genie_seconds=.2, genie_enabled=True), client, api_key=KEY, genie_connector=connector)
            context = CallContext(episode_id="smoke", prompt_id="world-v1", purpose="smoke")
            first = asyncio.create_task(gateway.probe_world(prompt="A quiet street", context=context))
            await entered.wait()
            with pytest.raises(GatewayError) as error:
                await gateway.probe_world(prompt="Another street", context=context)
            assert error.value.code == ErrorCode.CONCURRENCY and calls == ["/v1beta/interactions"]
            release.set()
            await first
    asyncio.run(run())


def test_dispatched_genie_with_expired_lease_still_blocks_with_termination(tmp_path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("unexpected network request"))) as client:
            gateway = ModelGateway(settings(tmp_path, genie_enabled=True), client, api_key=KEY)
            context = CallContext(episode_id="smoke", prompt_id="world-v1", purpose="smoke")
            call = gateway._prepare(Service.GENIE, "world", {"create": {"model": "genie-4"}}, context, 1)
            reserve = lambda: gateway.ledger.reserve(call, "call", "credential", "genie-4", 1, 1, 60)
            identifier = reserve()
            gateway.ledger.dispatched(identifier)
            with pytest.raises(GatewayError) as live:
                reserve()
            assert live.value.code == ErrorCode.CONCURRENCY
            # The owner crashed: nobody will finish or cancel this dispatched world.
            with gateway.ledger.connection() as db:
                db.execute("UPDATE attempts SET lease_until=? WHERE request_id=?", (time.time() - 1, identifier))
            with pytest.raises(GatewayError) as expired:
                reserve()
            assert expired.value.code == ErrorCode.TERMINATION
    asyncio.run(run())


def test_live_genie_world_does_not_take_a_slot_from_other_calls(tmp_path):
    answers = {"visible": {"type": "noul", "noul": .9, "confidence": .8}}
    entered, release, both = asyncio.Event(), asyncio.Event(), asyncio.Event()
    in_flight = 0
    class Blocking(WorldSession):
        async def receive(self):
            entered.set()
            await release.wait()
            async for event in super().receive():
                yield event
    @asynccontextmanager
    async def connector(key, identifier, timeout):
        yield Blocking()
    async def handler(request):
        nonlocal in_flight
        if request.url.path != "/v1beta/decisions":
            return httpx.Response(200, json={"id": "world-1", "status": "in_progress"})
        in_flight += 1
        if in_flight == 2:
            both.set()
        await both.wait()  # Each call finishes only once both run at the same time.
        return httpx.Response(200, json={"answers": answers, "usage": {"total_input_tokens": 10, "total_output_tokens": 0, "total_tokens": 10}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path, max_concurrency=2, genie_seconds=5, genie_enabled=True), client,
                                   api_key=KEY, genie_connector=connector)
            context = CallContext(episode_id="smoke", prompt_id="world-v1", purpose="smoke")
            world = asyncio.create_task(gateway.probe_world(prompt="A quiet street", context=context))
            await entered.wait()
            questions = {"visible": {"type": "noul", "instructions": "Is it visible?"}}
            results = await asyncio.wait_for(asyncio.gather(*(gateway.judge(
                stage="image_gate", input_value="Street", context=CONTEXT, questions=questions) for _ in range(2))), 1)
            assert [result.output for result in results] == [answers] * 2
            release.set()
            await world
    asyncio.run(run())


def test_expired_undispatched_genie_row_is_released(tmp_path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("unexpected network request"))) as client:
            gateway = ModelGateway(settings(tmp_path, genie_enabled=True), client, api_key=KEY)
            context = CallContext(episode_id="smoke", prompt_id="world-v1", purpose="smoke")
            call = gateway._prepare(Service.GENIE, "world", {"create": {"model": "genie-4"}}, context, 1)
            reserve = lambda: gateway.ledger.reserve(call, "call", "credential", "genie-4", 1, 1, 60)
            crashed = reserve()  # The owner died after reserving, before sending anything.
            with gateway.ledger.connection() as db:
                db.execute("UPDATE attempts SET lease_until=? WHERE request_id=?", (time.time() - 1, crashed))
            assert reserve() != crashed
            assert gateway.ledger.get(crashed)["outcome"] == "cancelled"
            assert gateway.ledger.get(crashed)["charged_microusd"] == 1
    asyncio.run(run())


@pytest.mark.parametrize("reclaimed", [False, True])
def test_expired_genie_reservation_cannot_be_dispatched_by_its_old_worker(tmp_path, reclaimed):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("unexpected network request"))) as client:
            config = settings(tmp_path)
            old_worker = ModelGateway(config, client, api_key=KEY)
            new_worker = ModelGateway(config, client, api_key=KEY)
            call = old_worker._prepare(Service.GENIE, "world", {"create": {"model": "genie-4"}},
                CallContext(episode_id="smoke", prompt_id="world-v1", purpose="smoke"), 1)
            reserve = lambda gateway: gateway.ledger.reserve(call, "call", "credential", "genie-4", 1, 1, 60)
            expired = reserve(old_worker)
            with old_worker.ledger.connection() as db:
                db.execute("UPDATE attempts SET lease_until=? WHERE request_id=?", (time.time() - 1, expired))
            if reclaimed:
                current = reserve(new_worker)
                new_worker.ledger.dispatched(current)
            with pytest.raises(GatewayError) as error:
                old_worker.ledger.dispatched(expired)
            assert error.value.code == ErrorCode.CANCELLED
            row = old_worker.ledger.get(expired)
            assert row["dispatched"] == 0 and row["charged_microusd"] == 1
            if reclaimed:
                assert row["outcome"] == "cancelled"
                assert new_worker.ledger.get(current)["outcome"] == "pending"
                assert new_worker.ledger.budget_status()["unresolved_world_requests"] == [current]
    asyncio.run(run())


def test_gateway_does_not_send_after_another_worker_reclaims_its_reservation(tmp_path, monkeypatch):
    async def run():
        requests = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(400))) as client:
            config = settings(tmp_path, genie_enabled=True)
            old_worker = ModelGateway(config, client, api_key=KEY)
            new_worker = ModelGateway(config, client, api_key=KEY)
            call = old_worker._prepare(Service.GENIE, "world", {"create": {"model": "genie-4"}},
                CallContext(episode_id="smoke", prompt_id="world-v1", purpose="smoke"), 1)
            original_artifact = old_worker.ledger.artifact
            reservations = []

            def delayed_artifact(content):
                fingerprint = original_artifact(content)
                if not reservations:
                    # The first worker resumes after a stalled pre-dispatch write;
                    # meanwhile a second process has reclaimed its expired lease.
                    old = old_worker.ledger.attempts()[0]
                    with old_worker.ledger.connection() as db:
                        db.execute("UPDATE attempts SET lease_until=? WHERE request_id=?",
                                   (time.time() - 1, old["request_id"]))
                    current = new_worker.ledger.reserve(call, "replacement", new_worker._credential_hash,
                                                       "genie-4", 1, 1, 60)
                    new_worker.ledger.dispatched(current)
                    reservations.extend([old, current])
                return fingerprint

            monkeypatch.setattr(old_worker.ledger, "artifact", delayed_artifact)
            with pytest.raises(GatewayError) as error:
                await old_worker._execute(call)
            assert error.value.code == ErrorCode.CANCELLED and not requests
            old, current = reservations
            row = old_worker.ledger.get(old["request_id"])
            assert row["outcome"] == "cancelled" and row["dispatched"] == 0
            assert row["charged_microusd"] == old["charged_microusd"]
            assert new_worker.ledger.budget_status()["unresolved_world_requests"] == [current]
    asyncio.run(run())


def test_no_key_status_endpoint_and_smoke_never_send_requests(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    config = settings(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(config.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("GATEWAY_CONFIG", str(config_path))
    with TestClient(app) as client:
        status = client.get("/api/models/status").json()
        assert all(s["state"] == "unavailable" for s in status["services"])
        assert client.get("/api/health").status_code == 200
        assert client.post("/api/models/status").status_code == 405
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("unexpected model call"))) as client:
            gateway = ModelGateway(config, client)
            report = await run_smoke(gateway, list(Service))
            assert not report["all_selected_verified"]
            assert len(report["results"]) == 5
            assert all(r["error"]["code"] == "not_configured" for r in report["results"] if r["outcome"] != "skipped")
            assert report["results"][-1]["outcome"] == "skipped"
            assert report["status"]["budgets"]["charged_usd"] == 0
    asyncio.run(run())


def test_expired_pricing_and_invalid_config_fail_closed(tmp_path):
    with pytest.raises(ValidationError):
        settings(tmp_path, total_usd="NaN")
    with pytest.raises(ValidationError):
        settings(tmp_path, demo_reserve_usd="21")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("unexpected model call"))) as client:
            config = settings(tmp_path).model_copy(update={"rates_valid_through": date(2020, 1, 1)})
            gateway = ModelGateway(config, client, api_key=KEY)
            assert gateway.status()["rates_expired"]
            with pytest.raises(GatewayError) as error:
                await observe(gateway)
            assert error.value.code == ErrorCode.BUDGET
    asyncio.run(run())


def test_genie_rejected_create_resolves_and_next_probe_can_start(tmp_path):
    creates = []
    @asynccontextmanager
    async def connector(*_):
        yield WorldSession()
    def handler(request):
        creates.append(request.url.path)
        # A 400 (e.g. a refused prompt) means no interaction exists.
        if len(creates) == 1:
            return httpx.Response(400, json={"error": "rejected"})
        return httpx.Response(200, json={"id": "world-1", "status": "in_progress"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path, genie_seconds=.2, genie_enabled=True), client, api_key=KEY, genie_connector=connector)
            context = CallContext(episode_id="smoke", prompt_id="world-v1", purpose="smoke")
            with pytest.raises(GatewayError) as error:
                await gateway.probe_world(prompt="A branded street", context=context)
            assert error.value.code == ErrorCode.INVALID_INPUT
            assert gateway.ledger.get(error.value.request_id)["outcome"] == ErrorCode.INVALID_INPUT.value
            assert not gateway.status()["budgets"]["unresolved_world_requests"]
            result = await gateway.probe_world(prompt="A quiet street", context=context)
            assert result.output["termination"] == "interaction.completed"
    asyncio.run(run())


def test_genie_cancellation_during_cleanup_stays_unresolved_and_cancellable(tmp_path):
    in_cleanup = asyncio.Event()
    cancellation_works = False
    @asynccontextmanager
    async def connector(*_):
        yield WorldSession(fail=True)
    async def handler(request):
        if request.url.path.endswith("/cancel"):
            if cancellation_works:
                return httpx.Response(200, json={"id": "world-1", "status": "cancelled"})
            in_cleanup.set()
            await asyncio.sleep(10)
        return httpx.Response(200, json={"id": "world-1", "status": "in_progress"})
    async def run():
        nonlocal cancellation_works
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path, genie_enabled=True), client, api_key=KEY, genie_connector=connector)
            context = CallContext(episode_id="smoke", prompt_id="world-v1", purpose="smoke")
            task = asyncio.create_task(gateway.probe_world(prompt="Street", context=context))
            await asyncio.wait_for(in_cleanup.wait(), 2)
            task.cancel()  # e.g. shutdown while the cleanup cancel is in flight
            with pytest.raises((GatewayError, asyncio.CancelledError)):
                await task
            unresolved = gateway.status()["budgets"]["unresolved_world_requests"]
            assert len(unresolved) == 1
            assert gateway.ledger.get(unresolved[0])["outcome"] == ErrorCode.TERMINATION.value
            cancellation_works = True
            assert (await gateway.cancel_world(unresolved[0]))["termination"] == "cancelled"
            assert not gateway.status()["budgets"]["unresolved_world_requests"]
    asyncio.run(run())

def test_known_world_recovery_bypasses_disabled_budget_and_preserves_failure(tmp_path):
    cancellation_works = False
    @asynccontextmanager
    async def connector(*_):
        yield WorldSession(fail=True)
    def handler(request):
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"id": "world-1", "status": "cancelled"}) if cancellation_works else httpx.Response(503)
        return httpx.Response(200, json={"id": "world-1", "status": "in_progress"})
    async def run():
        nonlocal cancellation_works
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            config = settings(tmp_path, genie_enabled=True)
            gateway = ModelGateway(config, client, api_key=KEY, genie_connector=connector)
            context = CallContext(episode_id="smoke", prompt_id="world-v1", purpose="smoke")
            with pytest.raises(GatewayError) as error:
                await gateway.probe_world(prompt="Street", context=context)
            request_id = error.value.request_id
            original = gateway.ledger.get(request_id)
            with pytest.raises(GatewayError):
                await gateway.probe_world(prompt="Street", context=context)
            assert len(gateway.status()["budgets"]["unresolved_world_requests"]) == 1
            cancellation_works = True
            disabled = ModelGateway(config.model_copy(update={"enabled": False}), client, api_key=KEY)
            result = await disabled.cancel_world(request_id)
            assert result["termination"] == "cancelled"
            assert not disabled.status()["budgets"]["unresolved_world_requests"]
            assert disabled.ledger.get(request_id)["charged_microusd"] == original["charged_microusd"]
            evidence = json.loads((config.data_dir / "artifacts" / (result["result_hash"] + ".json")).read_bytes())
            assert evidence["previous_result_hash"] == original["result_hash"]
            assert (config.data_dir / "artifacts" / (original["result_hash"] + ".json")).exists()
    asyncio.run(run())


def test_response_size_limit_and_secret_echo_do_not_escape(tmp_path):
    async def run():
        payload = response(text=KEY)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
            gateway = ModelGateway(settings(tmp_path, max_response_bytes=1024), client, api_key=KEY)
            result = await gateway.generate(Service.HYDROGEN, stage="observation", input_value="Describe", context=CONTEXT)
            assert result.output == "[REDACTED]"
            payload["steps"][0]["content"][0]["text"] = "x" * 2048
            with pytest.raises(GatewayError) as error:
                await observe(gateway)
            assert error.value.code == ErrorCode.INVALID_OUTPUT
    asyncio.run(run())
    assert all(KEY.encode() not in p.read_bytes() for p in (tmp_path / "evidence").rglob("*") if p.is_file())


def test_smoke_accepts_omitted_optional_provider_id_but_requires_usage(tmp_path):
    payload = response(text='{"visible_vehicle_count":2,"description":"Two cars on the illustrated road."}', id=None)
    def handler(request):
        return httpx.Response(200, json=payload)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            report = await run_smoke(gateway, [Service.HYDROGEN])
            assert report["results"][0]["outcome"] == "success"
            assert report["all_selected_verified"]
            assert report["results"][0]["provider_request_id"] is None
            assert report["results"][0]["provider_request_id_status"] == "not_returned"
            assert gateway.ledger.get(report["results"][0]["request_id"])["result_hash"]
            payload.pop("usage")
            report = await run_smoke(gateway, [Service.HYDROGEN])
            assert not report["all_selected_verified"]
    asyncio.run(run())


def test_example_configuration_is_valid():
    from pathlib import Path
    config = GatewayConfig.model_validate_json((Path(__file__).resolve().parent.parent / "gateway.example.json").read_text())
    assert config.rates[Service.CARBON].output_per_million == Decimal("10")


def test_selected_workshop_key_never_falls_back_and_status_has_no_secret(tmp_path, monkeypatch):
    from backend.gateway.config import backend_key
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setenv("GOOGLE_API_KEY", "primary-google-alias-secret")
    monkeypatch.setenv("WORKSHOP_API_KEY", "")
    monkeypatch.setenv("GEMINI_KEY_PROFILE", "workshop")
    config = GatewayConfig.from_environment()
    assert config.key_profile == "workshop" and backend_key("workshop") is None
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("unexpected dispatch"))) as client:
            gateway = ModelGateway(config, client)
            assert gateway.status()["key_profile"] == "workshop"
            assert KEY not in json.dumps(gateway.status()) + repr(config)
            with pytest.raises(GatewayError) as error:
                await observe(gateway)
            assert error.value.code == ErrorCode.NOT_CONFIGURED
    asyncio.run(run())


@pytest.mark.parametrize("profile,primary,expected", [
    ("primary", "", "primary-google-alias-secret"),
    ("primary", KEY, KEY),
    ("workshop", KEY, "workshop-selected-secret"),
])
def test_selected_profile_preserves_primary_alias_and_dispatches_only_its_key(tmp_path, monkeypatch, profile, primary, expected):
    monkeypatch.setenv("GEMINI_API_KEY", primary)
    monkeypatch.setenv("GOOGLE_API_KEY", "primary-google-alias-secret")
    monkeypatch.setenv("WORKSHOP_API_KEY", "workshop-selected-secret")
    sent_keys = []
    def handler(request):
        sent_keys.append(request.headers["x-goog-api-key"])
        return httpx.Response(200, json=response())
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path, key_profile=profile), client)
            assert (await observe(gateway)).output == {"count": 2}
            assert sent_keys == [expected]
            status = json.dumps(gateway.status())
            assert all(secret not in status for secret in (KEY, "primary-google-alias-secret", "workshop-selected-secret"))
    asyncio.run(run())


def test_legacy_environment_caps_and_relative_paths_preserved(tmp_path, monkeypatch):
    from backend.gateway.config import ROOT
    monkeypatch.setenv("MODEL_TOTAL_BUDGET_USD", "21")
    monkeypatch.setenv("MODEL_STAGE_BUDGET_USD", "7")
    monkeypatch.setenv("MODEL_STAGE_TOKEN_BUDGET", "131072")
    monkeypatch.setenv("MODEL_MAX_REQUEST_TOKENS", "16384")
    monkeypatch.setenv("MODEL_MAX_REQUEST_USD", "4")
    monkeypatch.setenv("MODEL_LEDGER_PATH", "data/legacy-test.sqlite3")
    config = GatewayConfig.from_environment()
    assert config.total_usd == 21 and config.demo_reserve_usd == 5
    assert config.stages["observation"].tokens == 131072
    assert config.service_budgets[Service.HYDROGEN].usd == 7
    assert config.input_tokens_per_call == 12288 and config.max_request_tokens == 16384
    assert config.legacy_ledger_path == ROOT / "data/legacy-test.sqlite3"
    assert config.genie_enabled is False
    config_path = tmp_path / "explicit.json"
    config_path.write_text(json.dumps({"input_tokens_per_call": 32768, "output_tokens_per_call": 4096}))
    monkeypatch.setenv("GATEWAY_CONFIG", str(config_path))
    with pytest.raises(ValueError, match="allowances"):
        GatewayConfig.from_environment()


def test_legacy_reservations_import_once_and_aggregate_across_stages(tmp_path):
    import sqlite3
    path = tmp_path / "legacy.sqlite3"
    rows = [("hydrogen", 131072, 7), ("carbon", 32768, 3), ("nano_banana", 16384, 1.25),
            ("decisions", 2048, 0), ("genie", 25705, 4)]
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE attempts(id TEXT PRIMARY KEY,service TEXT,profile TEXT,tokens INTEGER,usd REAL,data TEXT)")
        for index, (service, tokens, usd) in enumerate(rows):
            record = {"promptId": "legacy-v1", "configId": "legacy-v1", "inputSha256": "a" * 64,
                      "startedAt": "2026-11-21T12:00:00Z", "completedAt": "2026-11-21T12:00:01Z",
                      "outcome": "success", "originalMediaSha256": ["b" * 64]}
            db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?)", (str(index), service, "primary", tokens, usd, json.dumps(record)))
    original = path.read_bytes()
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("unexpected dispatch"))) as client:
            config = settings(tmp_path, legacy_ledger_path=path, total_usd=Decimal("21"),
                              service_budgets={service: StageBudget(tokens=131072, usd="7") for service in Service})
            for _ in range(2):
                gateway = ModelGateway(config, client, api_key=KEY)
                assert gateway.ledger.budget_status()["charged_usd"] == 15.25
                assert sum(row["charged_tokens"] for row in gateway.ledger.attempts()) == 207977
                assert len(gateway.ledger.attempts()) == 5
                assert gateway.status()["services"][0]["state"] == "unverified"
            with pytest.raises(GatewayError) as error:
                await gateway.generate(Service.HYDROGEN, stage="navigation", input_value="Navigate", context=CONTEXT)
            assert error.value.code == ErrorCode.BUDGET
            assert gateway.ledger.budget_status()["charged_usd"] == 15.25
    asyncio.run(run())
    assert path.read_bytes() == original


def test_disabled_genie_blocks_before_reservation_and_all_smoke_skips(tmp_path, monkeypatch):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("unexpected dispatch"))) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            monkeypatch.setattr(gateway.ledger, "reserve", lambda *args: pytest.fail("unexpected reservation"))
            context = CallContext(episode_id="smoke", prompt_id="v1", purpose="smoke")
            with pytest.raises(GatewayError) as error:
                await gateway.probe_world(prompt="Street", context=context)
            assert error.value.code == ErrorCode.DISABLED
            assert gateway.ledger.budget_status()["charged_usd"] == 0
            assert all(not row["dispatched"] for row in gateway.ledger.attempts())
            status = gateway.status()["services"][-1]
            assert status["optional"] is True and status["enabled"] is False and status["reason"] == "disabled"
            report = await run_smoke(gateway, [Service.GENIE])
            assert report["results"][0]["outcome"] == "skipped"
            assert not report["all_selected_verified"]
            report = await run_smoke(gateway, [Service.GENIE], skip_disabled=False)
            assert report["results"][0]["error"]["code"] == "disabled"
            assert not report["all_selected_verified"]
    asyncio.run(run())


def test_schema_projection_keeps_local_bounds_and_large_frame_envelopes(tmp_path):
    payloads = []
    class Bounded(BaseModel):
        count: int = Field(ge=0, le=3)
        objects: list[str] = Field(min_length=1, max_length=4)
    def handler(request):
        payloads.append(json.loads(request.content))
        assert request.headers["Api-Revision"] == "2026-05-20"
        return httpx.Response(200, json=response(text='{"count":-1,"objects":[]}'))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            parts = [{"type": "text", "text": "Inspect immutable frames"}] + [fixture_image()] * 121
            with pytest.raises(GatewayError) as error:
                await gateway.generate(Service.HYDROGEN, stage="observation", input_value=parts,
                                       context=CONTEXT, response_model=Bounded)
            assert error.value.code == ErrorCode.INVALID_OUTPUT
    asyncio.run(run())
    schema = payloads[0]["response_format"]["schema"]
    assert "minimum" not in schema["properties"]["count"]
    assert "maxItems" not in schema["properties"]["objects"]
    assert schema["required"] == ["count", "objects"]
    assert schema["properties"]["objects"]["items"] == {"type": "string"}
    assert Bounded.model_json_schema()["properties"]["count"]["minimum"] == 0


def test_model_text_excludes_sources_and_trace_validates_against_shared_contract(tmp_path):
    from backend.contracts import ModelCall
    from backend.modelcalls import from_result
    from backend.artifacts import utcnow
    payload = response(id="interactions/provider-1", steps=[
        {"type": "user_input", "content": [{"type": "text", "text": "Untrusted source"}]},
        {"type": "thought", "content": [{"type": "text", "text": "Hidden reasoning"}]},
        {"type": "model_output", "content": [{"type": "text", "text": '{"count":'}]},
        {"type": "model_output", "content": [{"type": "text", "text": '2}'}]},
    ])
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            result = await observe(gateway)
            trace = from_result(gateway, Service.HYDROGEN, result, prompt_id="observe-v1", domain_hashes=[],
                                requested_at=utcnow(), completed_at=utcnow())
            ModelCall.model_validate(trace)
            assert result.output == {"count": 2}
            assert trace.requestId == "interactions/provider-1"
            assert trace.inputHashes == ["sha256:" + result.input_hash]
            assert trace.outcome == "ok"
    asyncio.run(run())


def test_incomplete_usage_is_retained_and_continuation_token_is_not(tmp_path):
    payload = response(status="incomplete", steps=[], continuation_token="private-continuation-value")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            with pytest.raises(GatewayError) as error:
                await observe(gateway)
            assert error.value.code == ErrorCode.INCOMPLETE_OUTPUT
            record = gateway.ledger.attempts()[0]
            assert json.loads(record["usage"])["total_tokens"] == 22
            assert len(gateway.ledger.attempts()) == 1
    asyncio.run(run())
    assert all(b"private-continuation-value" not in path.read_bytes() for path in (tmp_path / "evidence").rglob("*") if path.is_file())


@pytest.mark.parametrize("provider_code,expected", [("invalid_request", "invalid_request"), (KEY, None)])
def test_provider_code_fallback_is_allowlisted_and_errors_not_persisted(tmp_path, provider_code, expected):
    payload = {"error": {"code": provider_code, "message": "private " + KEY}}
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(400, json=payload))) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            with pytest.raises(GatewayError) as error:
                await observe(gateway)
            assert error.value.code == ErrorCode.INVALID_INPUT
            assert error.value.http_status == 400 and error.value.provider_status == expected
            assert KEY not in json.dumps(error.value.public())
    asyncio.run(run())
    assert all(KEY.encode() not in path.read_bytes() for path in (tmp_path / "evidence").rglob("*") if path.is_file())


def test_repeated_cancellation_during_genie_cleanup_latches_termination(tmp_path):
    async def run():
        entered, cancelling = asyncio.Event(), asyncio.Event()
        @asynccontextmanager
        async def connector(*args):
            entered.set()
            await asyncio.sleep(10)
            yield WorldSession()
        async def handler(request):
            if request.url.path.endswith("/cancel"):
                cancelling.set()
                await asyncio.sleep(10)
            return httpx.Response(200, json={"id": "world-1", "status": "in_progress"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            config = settings(tmp_path, genie_enabled=True)
            gateway = ModelGateway(config, client, api_key=KEY, genie_connector=connector)
            context = CallContext(episode_id="smoke", prompt_id="v1", purpose="smoke")
            task = asyncio.create_task(gateway.probe_world(prompt="Street", context=context))
            await entered.wait()
            task.cancel()
            await cancelling.wait()
            task.cancel()
            with pytest.raises((asyncio.CancelledError, GatewayError)):
                await task
            row = gateway.ledger.attempts()[0]
            assert row["outcome"] == "termination_unconfirmed" and row["charged_tokens"] > 0
            assert gateway.status()["budgets"]["unresolved_world_requests"] == [row["request_id"]]
            again = ModelGateway(config, client, api_key=KEY)
            with pytest.raises(GatewayError) as error:
                await again.probe_world(prompt="Street", context=context)
            assert error.value.code == ErrorCode.TERMINATION
    asyncio.run(run())


def test_submission_waits_for_domain_artifact_acceptance_and_binds_hash(tmp_path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response()))) as client:
            gateway = ModelGateway(settings(tmp_path), client, api_key=KEY)
            context = CallContext(episode_id="episode", source_id="camera", capture_id="capture",
                                  prompt_id="v1", purpose="submission")
            args = {"episode_id": "episode", "source_id": "camera", "capture_id": "capture"}
            result = await gateway.generate(Service.HYDROGEN, stage="observation", input_value="Count",
                                            context=context, response_model=Observation, qualify=False)
            await gateway.generate(Service.CARBON, stage="compilation", input_value="Compile", context=context, response_model=Observation)
            # Provider JSON passed, but domain frame references/ownership have
            # not been accepted. It must not qualify as submission evidence.
            with pytest.raises(GatewayError):
                gateway.require_submission_models(**args)
            assert gateway.ledger.get(result.request_id)["qualifying"] == 0
            binding = gateway.accept_artifact(result, artifact_id="observation-1", artifact_sha256="a" * 64)
            assert gateway.accept_artifact(result, artifact_id="observation-1", artifact_sha256="a" * 64) == binding
            assert gateway.require_submission_models(**args)["ready"]
            content = json.loads((gateway.config.data_dir / "artifacts" / (binding + ".json")).read_bytes())
            assert content["artifact_sha256"] == "a" * 64 and content["result_hash"] == result.result_hash
            with pytest.raises(GatewayError):
                gateway.accept_artifact(result, artifact_id="observation-1", artifact_sha256="b" * 64)
    asyncio.run(run())


@pytest.mark.parametrize("cleanup_outcome", ["acknowledged", "rejected", "timeout"])
def test_cancelled_probe_finishes_bounded_cleanup_and_preserves_world_latch(tmp_path, cleanup_outcome):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []

        async def handler(request):
            requests.append(request.url.path)
            if request.url.path.endswith("/cancel"):
                entered.set()
                await release.wait()
                if cleanup_outcome == "rejected":
                    return httpx.Response(503)
                return httpx.Response(200, json={"status": "cancelled", "usage": USAGE})
            return httpx.Response(200, json={"id": "world-1"})

        @asynccontextmanager
        async def connector(*_):
            raise RuntimeError("connection failed")
            yield

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            config = settings(tmp_path, cleanup_seconds=.1, genie_enabled=True)
            gateway = ModelGateway(config, client, api_key=KEY, genie_connector=connector)
            context = CallContext(episode_id="smoke", prompt_id="world-v1", purpose="smoke")
            task = asyncio.create_task(gateway.probe_world(prompt="Street", context=context))
            await entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()  # A second cancellation must not detach cleanup either.
            if cleanup_outcome != "timeout":
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            assert task.cancelled()
            row = gateway.ledger.attempts()[0]
            assert row["provider_request_id"] == "world-1"
            unresolved = gateway.ledger.budget_status()["unresolved_world_requests"]
            if cleanup_outcome == "acknowledged":
                assert row["outcome"] == "cancelled" and row["usage"]
                assert unresolved == []
            else:
                assert row["outcome"] == "termination_unconfirmed"
                assert unresolved == [row["request_id"]]
                before = len(requests)
                with pytest.raises(GatewayError) as exc:
                    await gateway.probe_world(prompt="Another street", context=context)
                assert exc.value.code == ErrorCode.TERMINATION
                assert len(requests) == before
    asyncio.run(run())


@pytest.mark.parametrize("usage_source", ["cleanup", "terminal_failure"])
def test_failed_probe_reconciles_reported_usage_and_latches_overrun(tmp_path, usage_source):
    overrun = {"total_input_tokens": 1_000_000, "total_output_tokens": 2_000_000, "total_tokens": 3_000_000}
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"status": "cancelled", "usage": overrun})
        return httpx.Response(200, json={"id": "world-1"})

    class FailedWorld(WorldSession):
        async def receive(self):
            yield {"event_type": "interaction.failed", "usage": overrun}

    @asynccontextmanager
    async def connector(*_):
        if usage_source == "cleanup":
            raise RuntimeError("connection failed")
        yield FailedWorld()

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(settings(tmp_path, genie_enabled=True), client, api_key=KEY, genie_connector=connector)
            context = CallContext(episode_id="smoke", prompt_id="world-v1", purpose="smoke")
            with pytest.raises(GatewayError) as exc:
                await gateway.probe_world(prompt="Street", context=context)
            assert exc.value.code == (ErrorCode.TRANSPORT if usage_source == "cleanup" else ErrorCode.UPSTREAM)
            row = gateway.ledger.attempts()[0]
            assert json.loads(row["usage"])["total_tokens"] == 3_000_000
            assert row["charged_tokens"] == 3_000_000
            assert row["charged_microusd"] == 36_500_000
            assert gateway.ledger.budget_status()["blocks"]
            evidence = json.loads((gateway.config.data_dir / "artifacts" / f"{row['result_hash']}.json").read_bytes())
            assert evidence["usage"]["total_tokens"] == 3_000_000
            before = len(requests)
            with pytest.raises(GatewayError) as blocked:
                await observe(gateway)
            assert blocked.value.code == ErrorCode.BUDGET and len(requests) == before
    asyncio.run(run())
