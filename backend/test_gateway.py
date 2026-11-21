import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from backend.gateway import GatewayConfig, GatewayError, GatewayResult, ModelGateway

SECRET = "secret-do-not-log"


def config(tmp_path, **kwargs):
    return GatewayConfig(api_key=SECRET, ledger_path=tmp_path / "ledger.sqlite3", **{ "genie_enabled": True, **kwargs})


def text_result():
    return {"id": "provider-1", "status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": "Bicycles allowed; geometry unknown."}]}],
            "usage": {"total_input_tokens": 10, "total_output_tokens": 20, "total_tokens": 30}}


async def invoke(gateway, **kwargs):
    return await gateway.run("hydrogen", {"input": "immutable scene"}, prompt_id="observe-v1", config_id="config-v1", reserve_tokens=100, reserve_usd=.5, **kwargs)


def test_missing_selected_key_is_not_silent_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_KEY_PROFILE", "workshop")
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    monkeypatch.delenv("WORKSHOP_API_KEY", raising=False)
    monkeypatch.setenv("MODEL_LEDGER_PATH", str(tmp_path / "ledger.sqlite3"))
    async def check():
        gateway = ModelGateway.from_env()
        try:
            assert gateway.status()["keyProfile"] == "workshop"
            assert all(s["status"] == "unavailable" for s in gateway.status()["services"])
            with pytest.raises(GatewayError, match="not configured"):
                await invoke(gateway)
        finally:
            await gateway.close()
    asyncio.run(check())


def test_real_http_shape_metadata_and_no_secret_logging(tmp_path):
    async def handler(request):
        assert request.url.path == "/v1beta/interactions"
        assert request.headers["x-goog-api-key"] == SECRET
        assert request.headers["Api-Revision"] == "2026-05-20"
        body = json.loads(request.content)
        assert body["model"] == "gemini-4-hydrogen"
        assert body["store"] is False
        assert body["generation_config"]["max_output_tokens"] == 100
        return httpx.Response(200, json=text_result())
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config(tmp_path), client=client)
            assert gateway.status()["services"][0]["status"] == "configured"
            result = await invoke(gateway)
            assert result.trace["providerRequestId"] == "provider-1"
            assert result.trace["inputTokens"] == 10
            assert result.metadata["latencyMs"] >= 0
            assert gateway.status()["services"][0]["status"] == "connected"
            records = gateway.ledger.records()
            assert SECRET not in json.dumps(records) + json.dumps(gateway.status()) + repr(gateway.config)
            assert "immutable scene" not in json.dumps(records)
            await gateway.close()
    asyncio.run(check())


def test_retry_freezes_bytes_and_charges_every_attempt(tmp_path):
    original = {"input": "original"}
    seen = []
    async def handler(request):
        seen.append(request.content)
        original["input"] = "new street moment"
        return httpx.Response(503 if len(seen) == 1 else 200, json=text_result())
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config(tmp_path), client=client)
            result = await gateway.run("carbon", original, prompt_id="compile-v1", config_id="config-v1", reserve_tokens=100, reserve_usd=.5)
            assert seen[0] == seen[1]
            assert result.metadata["attempt"] == 2
            assert gateway.ledger.summary()["reservedUsd"] == 1
            assert gateway.ledger.records()[0]["errorCode"] == "transport"
            await gateway.close()
    asyncio.run(check())


@pytest.mark.parametrize("status,expected,calls", [(401,"unavailable",1),(403,"unavailable",1),(404,"unavailable",1),(429,"quota",2),(503,"transport",2)])
def test_errors_are_typed_sanitized_and_bounded(tmp_path, status, expected, calls):
    count = 0
    async def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(status, json={"error": {"message": SECRET}})
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config(tmp_path), client=client)
            with pytest.raises(GatewayError) as err:
                await invoke(gateway)
            assert err.value.code == expected
            assert SECRET not in json.dumps(err.value.public()) + json.dumps(gateway.ledger.records())
            assert count == calls
            await gateway.close()
    asyncio.run(check())


def test_timeout_bounded_and_concurrency_busy(tmp_path):
    async def handler(request):
        await asyncio.sleep(.1)
        return httpx.Response(200, json=text_result())
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config(tmp_path, timeout_seconds=.08, concurrency=1, retries=0), client=client)
            first = asyncio.create_task(invoke(gateway))
            await asyncio.sleep(.001)
            with pytest.raises(GatewayError) as busy:
                await invoke(gateway)
            assert busy.value.code == "busy"
            with pytest.raises(GatewayError) as timeout:
                await first
            assert timeout.value.code == "timeout"
            assert gateway.ledger.summary()["attempts"] == 1
            await gateway.close()
    asyncio.run(check())


def test_budget_demo_reserve_and_restart(tmp_path):
    async def handler(request):
        return httpx.Response(200, json=text_result())
    async def check():
        cfg = config(tmp_path, total_usd=1, demo_reserve_usd=.5)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(cfg, client=client)
            await invoke(gateway)
            await gateway.close()
            gateway = ModelGateway(cfg, client=client)
            with pytest.raises(GatewayError) as error:
                await invoke(gateway)
            assert error.value.code == "budget"
            await invoke(gateway, demo=True)
            with pytest.raises(GatewayError):
                await invoke(gateway, demo=True)
            assert gateway.ledger.summary()["attempts"] == 2
            await gateway.close()
    asyncio.run(check())


def test_stage_tokens_preserve_provider_overage(tmp_path):
    async def handler(request):
        result = text_result()
        result["usage"]["total_tokens"] = 150
        return httpx.Response(200, json=result)
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config(tmp_path, stage_tokens=200), client=client)
            result = await invoke(gateway)
            assert result.metadata["reservationExceeded"]
            with pytest.raises(GatewayError) as error:
                await invoke(gateway)
            assert error.value.code == "budget"
            await gateway.close()
    asyncio.run(check())


def test_decisions_validates_typed_probabilities(tmp_path):
    async def handler(request):
        assert request.url.path == "/v1beta/decisions"
        return httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 1.1, "confidence": .9}}})
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config(tmp_path), client=client)
            with pytest.raises(GatewayError) as error:
                await gateway.run("decisions", {"input": "pixels", "questions": {"q": {"type": "noul", "instructions": "Visible?"}}}, prompt_id="gate-v1", config_id="config-v1", reserve_tokens=100, reserve_usd=.5)
            assert error.value.code == "invalid_output"
            assert len(gateway.ledger.records()) == 1
            await gateway.close()
    asyncio.run(check())


def test_rejects_mutable_media_urls_and_missing_output(tmp_path):
    async def handler(request):
        return httpx.Response(200, json={"status": "completed", "steps": []})
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config(tmp_path), client=client)
            with pytest.raises(GatewayError) as error:
                await gateway.run("hydrogen", {"input": [{"uri": "https://camera/latest.jpg"}]}, prompt_id="v1", config_id="v1", reserve_tokens=100, reserve_usd=.5)
            assert error.value.code == "invalid_input"
            assert gateway.ledger.summary()["attempts"] == 0
            with pytest.raises(GatewayError) as error:
                await invoke(gateway)
            assert error.value.code == "invalid_output"
            await gateway.close()
    asyncio.run(check())


class FakeSession:
    def __init__(self, fail=False):
        self.completed = False
        self.sent = []
        self.fail = fail
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def send(self, payload): self.sent.append(payload)
    async def complete(self): self.completed = True
    async def cancel(self): self.completed = True
    async def receive(self):
        if self.fail:
            yield SimpleNamespace(event_type="error")
        else:
            yield SimpleNamespace(event_type="step.delta", delta=SimpleNamespace(type="video", data=base64.b64encode(b"\xff\xd8\xfftestframe").decode()))
        assert self.completed
        yield SimpleNamespace(event_type="interaction.completed", usage=None)


@pytest.mark.parametrize("fail", [False, True])
def test_genie_explicitly_completes_success_and_error(tmp_path, fail):
    session = FakeSession(fail)
    async def create(**kwargs):
        assert kwargs["model"] == "genie-4"
        return SimpleNamespace(id="interaction-1")
    async def close(): pass
    interactions = SimpleNamespace(create=create, connect=lambda *a, **kw: session)
    def factory(**kwargs):
        assert kwargs["api_key"] == SECRET
        return SimpleNamespace(aio=SimpleNamespace(interactions=interactions, aclose=close))
    async def check():
        gateway = ModelGateway(config(tmp_path), genie_client_factory=factory)
        try:
            call = gateway.run("genie", {"world_prompt": {"text": "Empty street"}}, prompt_id="world-v1", config_id="v1", reserve_tokens=100, reserve_usd=.5)
            if fail:
                with pytest.raises(GatewayError): await call
            else:
                result = await call
                assert result.output["completed"]
                assert result.trace["providerRequestId"] == "interaction-1"
            assert session.completed
            assert session.sent[0]["event_type"] == "world_setup"
            assert gateway.ledger.records()[0]["providerRequestId"] == "interaction-1"
        finally:
            await gateway.close()
    asyncio.run(check())


def test_config_rotation_invalid_input_and_trace_contract(tmp_path):
    from backend.contracts import ModelTrace
    async def handler(request): return httpx.Response(200, json=text_result())
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config(tmp_path), client=client)
            result = await invoke(gateway)
            assert ModelTrace.model_validate(result.trace).startedAt.endswith("Z")
            assert "credentialDigest" not in result.metadata
            await gateway.close()
            rotated = GatewayConfig(api_key="different-key", ledger_path=tmp_path / "ledger.sqlite3")
            gateway = ModelGateway(rotated, client=client)
            assert gateway.status()["services"][0]["status"] == "configured"
            with pytest.raises(GatewayError) as error:
                await gateway.run("hydrogen", {"input": "x", "generation_config": 3}, prompt_id="v1", config_id="v1", reserve_tokens=100, reserve_usd=.5)
            assert error.value.code == "invalid_input"
            await gateway.close()
    asyncio.run(check())


@pytest.mark.parametrize("phase", ["create", "connect", "receive"])
@pytest.mark.parametrize("cancel_caller", [False, True])
def test_genie_deadline_and_cancellation_cleanup(tmp_path, phase, cancel_caller):
    cancellation_ids = []
    entered = asyncio.Event()
    session = FakeSession()
    class SlowSession(FakeSession):
        async def __aenter__(self):
            if phase == "connect":
                entered.set()
                await asyncio.sleep(10)
            return self
        async def receive(self):
            if phase == "receive":
                entered.set()
                await asyncio.sleep(10)
            yield SimpleNamespace(event_type="interaction.completed")
    session = SlowSession()
    async def create(**kwargs):
        if phase == "create":
            entered.set()
            await asyncio.sleep(10)
        return SimpleNamespace(id="interaction-1")
    async def cancel(identifier, **kwargs):
        cancellation_ids.append(identifier)
        return SimpleNamespace(status="cancelled")
    async def close(): pass
    def factory(**kwargs):
        return SimpleNamespace(aio=SimpleNamespace(interactions=SimpleNamespace(create=create, connect=lambda *a, **kw: session, cancel=cancel), aclose=close))
    async def check():
        gateway = ModelGateway(config(tmp_path, timeout_seconds=.03), genie_client_factory=factory)
        try:
            task = asyncio.create_task(gateway.run("genie", {"world_prompt": {"text": "Empty street"}}, prompt_id="world-v1", config_id="v1", reserve_tokens=100, reserve_usd=.5))
            await entered.wait()
            if cancel_caller:
                task.cancel()
                with pytest.raises((asyncio.CancelledError, GatewayError)):
                    await task
            else:
                with pytest.raises(GatewayError) as error:
                    await task
                assert error.value.code in {"timeout", "termination_unconfirmed"}
            assert cancellation_ids == ([] if phase == "create" else ["interaction-1"])
            if phase != "create":
                assert gateway.ledger.records()[0]["remoteTermination"] == "cancelled"
        finally:
            await gateway.close()
    asyncio.run(check())


def test_unconfirmed_genie_cleanup_blocks_further_sessions(tmp_path):
    class BrokenConnect:
        async def __aenter__(self): raise ConnectionError("private details")
        async def __aexit__(self, *args): pass
    async def create(**kwargs): return SimpleNamespace(id="interaction-still-open")
    async def cancel(*args, **kwargs): raise ConnectionError(SECRET)
    async def close(): pass
    def factory(**kwargs):
        return SimpleNamespace(aio=SimpleNamespace(interactions=SimpleNamespace(create=create, connect=lambda *a, **kw: BrokenConnect(), cancel=cancel), aclose=close))
    async def check():
        gateway = ModelGateway(config(tmp_path), genie_client_factory=factory)
        try:
            for _ in range(2):
                with pytest.raises(GatewayError) as error:
                    await gateway.run("genie", {"world_prompt": {"text": "Empty street"}}, prompt_id="world-v1", config_id="v1", reserve_tokens=100, reserve_usd=.5)
                assert error.value.code == "termination_unconfirmed"
            records = gateway.ledger.records()
            assert len(records) == 1
            assert records[0]["providerRequestId"] == "interaction-still-open"
            assert SECRET not in json.dumps(records)
        finally:
            await gateway.close()
    asyncio.run(check())


def test_decisions_choice_and_score_semantics():
    choice_question = {"type": "choice", "criteria": {"allow": "allowed", "deny": "forbidden"}}
    score_question = {"type": "score", "criteria": ["low", "high"]}
    choice = {"type": "choice", "choice": "allow", "probabilities": {"allow": .9, "deny": .1}, "confidence": .9}
    score = {"type": "score", "score": .8, "probabilities": {"0": .2, "1": .8}, "legend": {"0": "low", "1": "high"}, "confidence": .8}
    request = {"questions": {"choice": choice_question, "score": score_question}}
    ModelGateway._validate_output("decisions", {"answers": {"choice": choice, "score": score}}, request)
    bad_choices = [
        {key: value for key, value in choice.items() if key != "choice"},
        {**choice, "choice": "invented"},
        {**choice, "choice": "deny"},
        {**choice, "probabilities": {"allow": .9, "invented": .1}},
        {**choice, "probabilities": {"allow": .9, "deny": .9}},
    ]
    bad_scores = [
        {key: value for key, value in score.items() if key != "score"},
        {**score, "score": 10},
        {**score, "score": .1},
        {**score, "probabilities": {"0": .2, "2": .8}},
        {**score, "legend": {"0": "changed", "1": "high"}},
    ]
    for broken in bad_choices:
        with pytest.raises(GatewayError):
            ModelGateway._validate_output("decisions", {"answers": {"choice": broken, "score": score}}, request)
    for broken in bad_scores:
        with pytest.raises(GatewayError):
            ModelGateway._validate_output("decisions", {"answers": {"choice": choice, "score": broken}}, request)


def test_incomplete_output_retains_usage_without_continuation_secret(tmp_path):
    async def handler(request):
        return httpx.Response(200, json={"status": "incomplete", "continuation_token": SECRET,
                                        "usage": {"total_input_tokens": 49, "total_output_tokens": 3, "total_thought_tokens": 125, "total_tokens": 177}})
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config(tmp_path), client=client)
            try:
                with pytest.raises(GatewayError) as error:
                    await invoke(gateway)
                assert error.value.code == "incomplete_output"
                records = gateway.ledger.records()
                assert len(records) == 1
                assert records[0]["inputTokens"] == 49
                assert records[0]["totalTokens"] == 177
                assert records[0]["providerOutcome"] == "incomplete"
                assert records[0]["httpStatus"] == 200
                assert SECRET not in json.dumps(records)
                assert "continuation_token" not in json.dumps(records)
            finally:
                await gateway.close()
    asyncio.run(check())


@pytest.mark.parametrize("provider_status,expected", [("PERMISSION_DENIED", "PERMISSION_DENIED"), (SECRET, None)])
def test_http_diagnostics_only_expose_allowlisted_status(tmp_path, provider_status, expected):
    async def handler(request):
        return httpx.Response(403, json={"error": {"status": provider_status, "message": SECRET, "details": [SECRET]}})
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config(tmp_path), client=client)
            try:
                with pytest.raises(GatewayError) as error:
                    await invoke(gateway)
                public = error.value.public()
                assert public["httpStatus"] == 403
                assert public["providerStatus"] == expected
                assert gateway.status()["services"][0]["providerStatus"] == expected
                assert SECRET not in json.dumps(public) + json.dumps(gateway.ledger.records())
            finally:
                await gateway.close()
    asyncio.run(check())


def test_text_extraction_matches_final_model_content():
    from backend.gateway import extract_model_text
    output = {"status": "completed", "steps": [
        {"type": "user_input", "content": [{"type": "text", "text": "must not echo"}]},
        {"type": "model_output", "content": [{"type": "text", "text": "earlier"}]},
        {"type": "function_call", "name": "ignored"},
        {"type": "thought", "content": [{"type": "text", "text": "must not expose"}]},
        {"type": "model_output", "content": [{"type": "text", "text": '{"observations":'}, {"type": "text", "text": "[]}"}]},
    ]}
    assert extract_model_text(output) == '{"observations":[]}'
    with pytest.raises(GatewayError):
        extract_model_text({"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": {"wrong": "type"}}]}]})
    with pytest.raises(GatewayError):
        extract_model_text({"status": "completed", "output_text": "SDK shortcut is not a REST response"})


def test_genie_sdk_auth_error_is_sanitized_and_classified(tmp_path):
    class SDKError(Exception):
        status_code = 403
        body = {"error": {"status": "PERMISSION_DENIED", "message": SECRET}}
    async def create(**kwargs): raise SDKError(SECRET)
    async def close(): pass
    def factory(**kwargs):
        return SimpleNamespace(aio=SimpleNamespace(interactions=SimpleNamespace(create=create, connect=lambda *a, **kw: None), aclose=close))
    async def check():
        gateway = ModelGateway(config(tmp_path), genie_client_factory=factory)
        try:
            with pytest.raises(GatewayError) as error:
                await gateway.run("genie", {"world_prompt": {"text": "Empty street"}}, prompt_id="world-v1", config_id="v1", reserve_tokens=100, reserve_usd=.5)
            assert error.value.code == "unavailable"
            assert error.value.public()["httpStatus"] == 403
            assert error.value.public()["providerStatus"] == "PERMISSION_DENIED"
            assert SECRET not in json.dumps(error.value.public()) + json.dumps(gateway.ledger.records())
        finally:
            await gateway.close()
    asyncio.run(check())


def test_image_stage_has_independent_output_budget(tmp_path):
    async def handler(request):
        body = json.loads(request.content)
        assert body["generation_config"]["max_output_tokens"] == 8192
        assert body["model"] == "gemini-nano-banana-2.1"
        return httpx.Response(200, json={"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "image", "mime_type": "image/png", "data": base64.b64encode(b"png-transport-test").decode()}]}]})
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config(tmp_path), client=client)
            try:
                result = await gateway.run("nano_banana", {"input": "Empty city street"}, prompt_id="image-v1", config_id="image-v1", reserve_tokens=16384, reserve_usd=1)
                assert result.trace["outcome"] == "success"
            finally:
                await gateway.close()
    asyncio.run(check())


def test_json_schema_field_names_are_not_media_references(tmp_path):
    async def handler(request):
        return httpx.Response(200, json=text_result())
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config(tmp_path), client=client)
            try:
                result = await gateway.run("hydrogen", {"input": "Describe output", "response_format": {"type": "text", "mime_type": "application/json", "schema": {"type": "object", "properties": {"url": {"type": "string"}}}}}, prompt_id="output-v1", config_id="output-v1", reserve_tokens=100, reserve_usd=.5)
                assert result.trace["outcome"] == "success"
            finally:
                await gateway.close()
    asyncio.run(check())


def test_genie_error_completion_does_not_count_as_success(tmp_path):
    class ErrorTerminal(FakeSession):
        async def receive(self):
            yield SimpleNamespace(event_type="step.delta", delta=SimpleNamespace(type="video", data=base64.b64encode(b"\xff\xd8\xffframe").decode()))
            yield SimpleNamespace(event_type="interaction.completed", status={"code": 8, "message": SECRET}, usage={"total_tokens": 240})
    session = ErrorTerminal()
    async def create(**kwargs): return SimpleNamespace(id="interaction-1")
    async def close(): pass
    def factory(**kwargs):
        return SimpleNamespace(aio=SimpleNamespace(interactions=SimpleNamespace(create=create, connect=lambda *a, **kw: session), aclose=close))
    async def check():
        gateway = ModelGateway(config(tmp_path), genie_client_factory=factory)
        try:
            with pytest.raises(GatewayError) as error:
                await gateway.run("genie", {"world_prompt": {"text": "Empty street"}}, prompt_id="world-v1", config_id="v1", reserve_tokens=100, reserve_usd=.5)
            assert error.value.code == "quota"
            record = gateway.ledger.records()[0]
            assert record["outcome"] == "error"
            assert record["providerRpcCode"] == 8
            assert record["totalTokens"] == 240
            assert session.completed
            assert SECRET not in json.dumps(record)
        finally:
            await gateway.close()
    asyncio.run(check())


@pytest.mark.parametrize("transport", ["http", "sdk"])
@pytest.mark.parametrize("provider_code,expected", [("invalid_request", "invalid_request"), (SECRET, None)])
def test_provider_error_code_fallback_is_allowlisted(tmp_path, transport, provider_code, expected):
    provider_body = {"error": {"code": provider_code, "message": SECRET}}
    async def handler(request): return httpx.Response(400, json=provider_body)
    class SDKError(Exception):
        status_code = 400
        body = provider_body
    async def create(**kwargs): raise SDKError(SECRET)
    async def close(): pass
    def factory(**kwargs):
        return SimpleNamespace(aio=SimpleNamespace(interactions=SimpleNamespace(create=create, connect=lambda *a, **kw: None), aclose=close))
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(config(tmp_path), client=client, genie_client_factory=factory)
            try:
                with pytest.raises(GatewayError) as error:
                    if transport == "http":
                        await invoke(gateway)
                    else:
                        await gateway.run("genie", {"world_prompt": {"text": "Empty street"}}, prompt_id="world-v1", config_id="v1", reserve_tokens=100, reserve_usd=.5)
                assert error.value.code == "invalid_input"
                assert error.value.public()["httpStatus"] == 400
                assert error.value.public()["providerStatus"] == expected
                assert gateway.ledger.records()[0].get("providerStatus") == expected
                assert SECRET not in json.dumps(error.value.public()) + json.dumps(gateway.ledger.records())
            finally:
                await gateway.close()
    asyncio.run(check())


def test_genie_disabled_by_default_without_spend_or_transport(tmp_path):
    from backend.gateway.core import utc_now
    calls = []
    async def handler(request):
        calls.append("http")
        raise AssertionError("Disabled Genie must never reach transport")
    def factory(**kwargs):
        calls.append("sdk")
        raise AssertionError("Disabled Genie must never create a client")
    async def check():
        cfg = GatewayConfig(api_key=SECRET, ledger_path=tmp_path / "ledger.sqlite3")
        assert cfg.genie_enabled is False
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(cfg, client=client, genie_client_factory=factory)
            try:
                historical = {"traceId": "trace_historical", "service": "genie", "profile": "primary",
                              "credentialDigest": gateway._credential_digest, "outcome": "success", "completedAt": utc_now()}
                gateway.ledger.reserve(historical, 100, .5, False)
                before = gateway.ledger.summary()
                with pytest.raises(GatewayError) as error:
                    await gateway.run("genie", {"world_prompt": {"text": "Empty street"}}, prompt_id="world-v1", config_id="v1", reserve_tokens=100, reserve_usd=.5)
                assert error.value.code == "disabled"
                assert error.value.retryable is False
                assert gateway.ledger.summary() == before
                assert gateway.ledger.records() == [historical]
                assert calls == []
                status = next(item for item in gateway.status()["services"] if item["service"] == "genie")
                assert status["optional"] is True
                assert status["enabled"] is False
                assert status["status"] == "unavailable"
                assert status["error"] == "disabled"
                assert status["checkedAt"] == historical["completedAt"]
            finally:
                await gateway.close()
    asyncio.run(check())


def test_genie_environment_requires_explicit_true(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    monkeypatch.setenv("GEMINI_KEY_PROFILE", "primary")
    monkeypatch.setenv("MODEL_LEDGER_PATH", str(tmp_path / "ledger.sqlite3"))
    monkeypatch.delenv("GENIE_ENABLED", raising=False)
    assert GatewayConfig.from_env().genie_enabled is False
    monkeypatch.setenv("GENIE_ENABLED", "true")
    assert GatewayConfig.from_env().genie_enabled is True
    monkeypatch.setenv("GENIE_ENABLED", "false")
    assert GatewayConfig.from_env().genie_enabled is False
    monkeypatch.setenv("GENIE_ENABLED", "yes")
    with pytest.raises(ValueError, match="GENIE_ENABLED"):
        GatewayConfig.from_env()


def test_all_smokes_skip_disabled_genie_but_explicit_probe_is_blocked(monkeypatch, capsys):
    from backend import smoke_models
    calls = []
    async def run(service, *args, **kwargs):
        calls.append(service)
        if service == "genie":
            raise GatewayError("disabled")
        return GatewayResult({}, {}, {"latencyMs": 1})
    async def close(): pass
    gateway = SimpleNamespace(config=SimpleNamespace(genie_enabled=False), run=run, close=close, status=lambda: {})
    monkeypatch.setattr(smoke_models, "load_environment", lambda: None)
    monkeypatch.setattr(smoke_models.ModelGateway, "from_env", lambda: gateway)
    assert asyncio.run(smoke_models.run_smokes(list(smoke_models.MODELS), skip_disabled=True)) == 0
    assert calls == ["hydrogen", "carbon", "nano_banana", "decisions"]
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(line.get("service") == "genie" and line.get("outcome") == "skipped" and line.get("reason") == "disabled" for line in lines)
    assert asyncio.run(smoke_models.run_smokes(["genie"])) == 1
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["error"]["code"] == "disabled"
