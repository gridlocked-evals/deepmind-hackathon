import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
import json
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from backend.gateway import CallContext, GatewayConfig, GatewayError, ModelGateway
from backend.gateway.contracts import ErrorCode, canonical, digest
from backend.gateway.smoke import fixture_image
from backend.worlds.api import router
from backend.worlds.contracts import AcceptedReference, WorldLimits, WorldRequest
from backend.worlds.evidence import ReferenceStore
from backend.worlds.runtime import WorldManager

KEY = "world-project-secret-not-for-clients"
SCOPED = "auth_tokens/session-secret-not-for-clients"
USAGE = {"total_input_tokens": 10, "total_output_tokens": 240, "total_tokens": 250}


class Stream:
    def __init__(self, complete=True, frames=True):
        self.queue = asyncio.Queue()
        self.sent = []
        self.completes = 0
        self.acknowledge = complete
        self.frames = frames
        self.closed = False

    async def send(self, value):
        self.sent.append(value)
        if value.get("event_type") == "world_setup" and self.frames:
            await self.frame()

    async def frame(self):
        await self.queue.put({"event_type": "step.delta", "delta": {
            "type": "video", "data": fixture_image()["data"]}})

    async def complete(self):
        self.completes += 1
        if self.acknowledge:
            await self.queue.put({"event_type": "interaction.completed", "usage": USAGE})

    async def receive(self):
        while True:
            value = await self.queue.get()
            if isinstance(value, Exception):
                raise value
            yield value


@asynccontextmanager
async def harness(tmp_path, *, stream=None, fail=None, limits=None, config_changes=None):
    stream = stream or Stream()
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/auth_tokens"):
            return httpx.Response(200, json={"auth_token": SCOPED})
        if request.url.path.endswith("/cancel"):
            return httpx.Response(503 if fail == "cancel" else 200,
                                  json={"status": "cancelled", "usage": USAGE})
        if fail == "create_timeout":
            raise httpx.ReadTimeout("hidden " + KEY)
        return httpx.Response(429 if fail == "quota" else 400 if fail == "rejected" else 200, json={"id": "world-1"})

    @asynccontextmanager
    async def connect(key, identifier, timeout):
        assert key == SCOPED
        assert identifier == "world-1"
        if fail == "connect":
            raise RuntimeError("do not leak " + KEY + SCOPED)
        try:
            yield stream
        finally:
            stream.closed = True

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        config = GatewayConfig(data_dir=tmp_path, cleanup_seconds=.2, rates_valid_through=date(2030, 1, 1),
                               **(config_changes or {}))
        gateway = ModelGateway(config, client, api_key=KEY, genie_connector=connect)
        manager = WorldManager(gateway, limits or WorldLimits(idle_seconds=2))
        try:
            yield manager, stream, requests
        finally:
            await manager.close()


def development(manager, **kwargs):
    image = fixture_image()
    return manager.development(prompt="A clearly labeled development street fixture.",
        image={"mime_type": image["mime_type"], "data": image["data"]}, seconds=kwargs.pop("seconds", 2), **kwargs)


async def start(session, roles=("human",)):
    for role in roles:
        await session.attach(role, session.tokens[role])
    await asyncio.wait_for(session.ready.wait(), 1)
    for _ in range(100):
        if session.recording.frame_index >= 0:
            return
        await asyncio.sleep(.001)


async def finish(session):
    await asyncio.wait_for(session.finished.wait(), 3)
    await session.task


def test_records_every_frame_scoped_auth_key_expiry_and_completed_usage(tmp_path):
    async def run():
        async with harness(tmp_path) as (manager, stream, requests):
            session = development(manager)
            await start(session)
            await session.command({"kind": "key", "owner": "human", "key": "key_w", "hold_ms": 30}, "human")
            await stream.frame()
            await stream.frame()
            await asyncio.sleep(.08)
            assert not session.held
            await session.stop()
            await finish(session)
            assert session.state == "completed"
            assert session.termination == "interaction.completed"
            assert stream.closed and stream.completes == 1
            assert len(list((session.recording.path / "frames").glob("*.jpg"))) == 3
            events = [json.loads(line) for line in (session.recording.path / "events.jsonl").read_text().splitlines()]
            assert [event["frame_index"] for event in events if event["kind"] == "frame"] == [0, 1, 2]
            assert any(event["kind"] == "key_up" for event in events)
            assert len([m for m in stream.sent if m["event_type"] == "world_setup"]) == 1
            assert "reference_image" in stream.sent[0] and "world_prompt" in stream.sent[0]
            assert manager.gateway.ledger.get(session.request_id)["usage"]
            assert not manager.gateway.ledger.budget_status()["unresolved_world_requests"]
            for path in tmp_path.rglob("*"):
                if path.is_file():
                    data = path.read_bytes()
                    assert KEY.encode() not in data and SCOPED.encode() not in data
    asyncio.run(run())


def test_takeover_releases_defender_and_keeps_steering_separate(tmp_path):
    async def run():
        async with harness(tmp_path) as (manager, stream, _):
            session = development(manager)
            await start(session, roles=("human", "defender", "adversary"))
            await session.command({"kind": "takeover", "owner": "defender"}, "human")
            await session.command({"kind": "key", "owner": "defender", "key": "arrow_left", "hold_ms": 500}, "defender")
            await session.command({"kind": "steer", "owner": "adversary", "text": "A blue bus emerges."}, "adversary")
            assert session.key_owner == "defender" and "arrow_left" in session.held
            await asyncio.sleep(.03)
            await session.command({"kind": "takeover", "owner": "human"}, "human")
            assert not session.held and session.key_owner == "human"
            assert all("blue bus" not in json.dumps(item) for item in list(session.queues["defender"]._queue))
            await session.stop()
            await finish(session)
    asyncio.run(run())


@pytest.mark.parametrize("reason", ["disconnect", "malformed", "emergency_stop", "heartbeat", "duration", "frame_timeout", "stream_error"])
def test_stop_paths_release_and_terminate(tmp_path, reason):
    async def run():
        limits = WorldLimits(idle_seconds=.2 if reason == "heartbeat" else 3,
                             frame_timeout=.2 if reason == "frame_timeout" else 12)
        async with harness(tmp_path, limits=limits) as (manager, stream, _):
            session = development(manager, seconds=1 if reason == "duration" else 2)
            await start(session)
            await session.command({"kind": "key", "owner": "human", "key": "key_w", "hold_ms": 500}, "human")
            if reason == "disconnect":
                await session.disconnected("human")
            elif reason == "malformed":
                with pytest.raises(GatewayError):
                    await session.command({"kind": "key", "hold_ms": 999999}, "human")
            elif reason == "emergency_stop":
                await session.command({"kind": "emergency_stop"}, "human")
            elif reason == "stream_error":
                await stream.queue.put(RuntimeError(KEY))
            await finish(session)
            assert session.termination in ("interaction.completed", "cancelled")
            assert not session.held
            assert any(m.get("action") == "key_up" for m in stream.sent)
            assert KEY not in json.dumps(session.status())
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["connect", "quota", "rejected", "create_timeout", "cancel"])
def test_failures_cancel_or_visibly_block_new_worlds(tmp_path, failure):
    async def run():
        async with harness(tmp_path, stream=Stream(complete=False), fail=failure) as (manager, _, requests):
            session = development(manager, seconds=1)
            await session.attach("human", session.tokens["human"])
            await finish(session)
            if failure in ("create_timeout", "cancel"):
                assert session.state == "termination_unconfirmed"
                assert manager.gateway.ledger.budget_status()["unresolved_world_requests"]
            else:
                assert session.state == "failed"
                assert not manager.gateway.ledger.budget_status()["unresolved_world_requests"]
            if failure == "quota":
                assert session.error == "quota_exceeded" and len(requests) == 1
            if failure == "connect":
                assert session.termination == "cancelled"
    asyncio.run(run())


def accepted_request(manager):
    image = fixture_image()
    data = base64.b64decode(image["data"])
    gate = canonical({"decision": "accept", "reference_sha256": digest(data), "development_fixture": True})
    record = AcceptedReference(episode_id="episode", source_id="source", capture_id="capture", reference_id="reference",
        reference_sha256=digest(data), mime_type=image["mime_type"], gate_decision_id="gate", gate_artifact_sha256=digest(gate), decision="accept")
    fingerprint = manager.references.publish(record, data, gate)
    request = WorldRequest(accepted_reference=fingerprint,
        context=CallContext(episode_id="episode", source_id="source", capture_id="capture", purpose="submission", prompt_id="world-v1"),
        prompt="The accepted street.", seconds=2)
    return request, record


@pytest.mark.parametrize("mutation", ["missing", "context", "image", "gate", "unknown", "development"])
def test_gate_fails_closed_before_any_paid_request(tmp_path, mutation):
    async def run():
        async with harness(tmp_path) as (manager, _, requests):
            request, record = accepted_request(manager)
            if mutation == "missing":
                request = request.model_copy(update={"accepted_reference": "0" * 64})
            elif mutation == "context":
                request = request.model_copy(update={"context": request.context.model_copy(update={"capture_id": "other"})})
            elif mutation == "development":
                request = request.model_copy(update={"context": request.context.model_copy(update={"purpose": "development"})})
            elif mutation == "unknown":
                with pytest.raises(ValidationError):
                    AcceptedReference.model_validate({**record.model_dump(), "decision": "insufficient_evidence"})
                return
            else:
                fingerprint = record.reference_sha256 if mutation == "image" else record.gate_artifact_sha256
                (manager.references.root / f"{fingerprint}.{mutation}").write_bytes(b"changed")
            with pytest.raises(GatewayError):
                manager.create(request)
            assert not requests
    asyncio.run(run())


def test_production_uses_exact_accepted_bytes_and_records_gate_hash(tmp_path):
    async def run():
        async with harness(tmp_path) as (manager, stream, _):
            request, record = accepted_request(manager)
            session = manager.create(request)
            await start(session)
            assert digest(base64.b64decode(stream.sent[0]["reference_image"]["data"])) == record.reference_sha256
            assert not session.status()["development_only"]
            assert session.provenance["gate_artifact_sha256"] == record.gate_artifact_sha256
            await session.stop()
            await finish(session)
    asyncio.run(run())


def test_duration_respects_dollar_ceiling_and_budget_admission(tmp_path):
    async def run():
        async with harness(tmp_path, config_changes={"total_usd": Decimal(".1"), "demo_reserve_usd": Decimal("0")}) as (manager, _, requests):
            with pytest.raises(GatewayError):
                development(manager, max_usd=Decimal(".01"))
            session = development(manager, seconds=30, max_usd=Decimal("1"))
            assert session.seconds < 10
            await session.attach("human", session.tokens["human"])
            await finish(session)
            assert session.error == "budget_exceeded" and not requests
    asyncio.run(run())


def test_unattached_session_never_dispatches_and_capability_cannot_be_reused(tmp_path):
    async def run():
        async with harness(tmp_path, limits=WorldLimits(attach_seconds=.1)) as (manager, _, requests):
            session = development(manager)
            await finish(session)
            assert session.reason == "connection_timeout" and not requests
            with pytest.raises(GatewayError):
                await session.attach("human", "wrong-token")
    asyncio.run(run())


def test_expired_crash_recovery_cancels_without_reopening_session(tmp_path):
    async def run():
        async with harness(tmp_path) as (manager, _, requests):
            session = development(manager)
            call = session.call
            gateway = manager.gateway
            identifier = gateway.ledger.reserve(call, "crashed-call", gateway._credential_hash, "genie-4", 1, 1, 1)
            gateway.ledger.dispatched(identifier)
            gateway.ledger.provider_id(identifier, "world-1")
            with gateway.ledger.connection() as db:
                db.execute("UPDATE attempts SET lease_until=? WHERE request_id=?", (time.time() - 1, identifier))
            await manager.recover()
            assert gateway.ledger.get(identifier)["outcome"] == "cancelled"
            assert len(requests) == 1 and requests[0].url.path.endswith("/cancel")
    asyncio.run(run())


def test_http_creation_requires_operator_token_and_rejects_client_gate(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(router)
    monkeypatch.delenv("WORLD_API_TOKEN", raising=False)
    with TestClient(app) as client:
        assert client.post("/api/worlds", json={}).status_code == 503
        monkeypatch.setenv("WORLD_API_TOKEN", "x" * 32)
        assert client.post("/api/worlds", json={}).status_code == 401
        response = client.post("/api/worlds", json={"accepted": True}, headers={"Authorization": "Bearer " + "x" * 32})
        assert response.status_code == 422


def test_browser_relay_capabilities_origin_disconnect_and_secret_boundary(tmp_path, monkeypatch):
    @asynccontextmanager
    async def lifespan(app):
        async with harness(tmp_path) as (manager, stream, requests):
            app.state.worlds = manager
            app.state.request_body = accepted_request(manager)[0].model_dump(mode="json")
            yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    monkeypatch.setenv("WORLD_API_TOKEN", "o" * 32)
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer " + "o" * 32}
        response = client.post("/api/worlds", json=app.state.request_body, headers=headers)
        assert response.status_code == 200
        second = client.post("/api/worlds", json=app.state.request_body, headers=headers)
        assert second.status_code == 409 and second.json()["detail"]["code"] == "concurrency_limit"
        body = response.json()
        assert KEY not in response.text and SCOPED not in response.text
        path, token = body["websocket_path"], body["connect_tokens"]["human"]
        session = app.state.worlds.sessions[body["session_id"]]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path, headers={"Origin": "https://untrusted.example"}):
                pass
        assert not session.stopping.is_set()
        with client.websocket_connect(path, headers={"Origin": "http://localhost:5173"}) as socket:
            socket.send_json({"role": "human", "token": token})
            assert socket.receive_json()["kind"] == "status"
            frame = socket.receive_json()
            assert frame["kind"] == "frame" and frame["frame_index"] == 0
            socket.send_json({"kind": "key", "owner": "human", "key": "key_w", "hold_ms": 100})
        client.portal.call(session.finished.wait)
        assert session.termination == "interaction.completed" and not session.held
        with client.websocket_connect(path) as socket:
            socket.send_json({"role": "human", "token": token})
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()


def test_missing_frames_do_not_verify_a_connected_world(tmp_path):
    async def run():
        async with harness(tmp_path, stream=Stream(frames=False), limits=WorldLimits(frame_timeout=.1)) as (manager, _, _):
            session = development(manager)
            await session.attach("human", session.tokens["human"])
            await finish(session)
            assert session.state == "failed" and session.error == "timeout"
            assert session.termination == "interaction.completed"
    asyncio.run(run())


def test_wrong_channel_cannot_steer_or_control_keys(tmp_path):
    async def run():
        async with harness(tmp_path) as (manager, stream, _):
            session = development(manager)
            await start(session, roles=("human", "adversary"))
            with pytest.raises(GatewayError):
                await session.command({"kind": "key", "owner": "human", "key": "key_w", "hold_ms": 100}, "adversary")
            await finish(session)
            assert not any(message.get("action") == "key_down" for message in stream.sent)
    asyncio.run(run())


def test_storage_failure_still_cancels_provider(tmp_path, monkeypatch):
    async def run():
        async with harness(tmp_path) as (manager, stream, requests):
            session = development(manager)
            def failed_frame(*args):
                raise OSError("disk failure")
            monkeypatch.setattr(session.recording, "frame", failed_frame)
            await session.attach("human", session.tokens["human"])
            await finish(session)
            assert session.state == "failed" and session.termination == "cancelled"
            assert requests[-1].url.path.endswith("/cancel")
    asyncio.run(run())


def test_caller_cancellation_during_cleanup_keeps_receiver_and_cancels(tmp_path):
    async def run():
        async with harness(tmp_path, stream=Stream(complete=False)) as (manager, stream, requests):
            session = development(manager)
            await start(session)
            await session.stop()
            while not stream.completes:
                await asyncio.sleep(.001)
            session.task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await finish(session)
            assert session.termination == "cancelled" and stream.closed
            assert session.state == "stopped" and session.error == "cancelled"
            assert requests[-1].url.path.endswith("/cancel")
            assert not manager.gateway.ledger.budget_status()["unresolved_world_requests"]
    asyncio.run(run())


def test_cancelled_world_with_unknown_creation_keeps_termination_block(tmp_path):
    async def run():
        entered = asyncio.Event()
        async with harness(tmp_path) as (manager, _, requests):
            async def pending_create(path, body, on_id):
                assert path == "/interactions"
                entered.set()
                await asyncio.Event().wait()

            manager.gateway.http.post = pending_create
            session = development(manager)
            await session.attach("human", session.tokens["human"])
            await entered.wait()
            session.task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await finish(session)
            assert session.state == "termination_unconfirmed" and session.provider_id is None
            row = manager.gateway.ledger.attempts()[0]
            assert row["outcome"] == "termination_unconfirmed"
            assert manager.gateway.ledger.budget_status()["unresolved_world_requests"] == [row["request_id"]]
            assert not requests  # No identifier was returned, so cancellation cannot target a world.
    asyncio.run(run())


@pytest.mark.parametrize("setup_stage", ["create", "auth_tokens", "connect"])
def test_stop_during_setup_never_sends_world_setup(tmp_path, setup_stage):
    async def run():
        entered, resume = asyncio.Event(), asyncio.Event()
        async with harness(tmp_path) as (manager, stream, requests):
            original_post = manager.gateway.http.post
            original_connect = manager.gateway.genie.connector

            async def post(path, body, on_id):
                if ((setup_stage == "create" and path == "/interactions") or
                        (setup_stage == "auth_tokens" and path.endswith("/auth_tokens"))):
                    entered.set()
                    await resume.wait()
                return await original_post(path, body, on_id)

            @asynccontextmanager
            async def connect(*args):
                if setup_stage == "connect":
                    entered.set()
                    await resume.wait()
                async with original_connect(*args) as connection:
                    yield connection

            manager.gateway.http.post = post
            manager.gateway.genie.connector = connect
            session = development(manager)
            await session.attach("human", session.tokens["human"])
            await entered.wait()
            await session.stop("emergency_stop")
            resume.set()
            await finish(session)
            assert not any(item.get("event_type") == "world_setup" for item in stream.sent)
            assert session.recording.frame_index == -1 and not session.ready.is_set()
            assert session.state == "stopped" and session.reason == "emergency_stop"
            assert session.termination in ("cancelled", "interaction.completed")
            assert manager.gateway.ledger.get(session.request_id)["outcome"] == "cancelled"
            assert not manager.gateway.ledger.budget_status()["unresolved_world_requests"]
            if setup_stage == "create":
                assert not any(request.url.path.endswith("/auth_tokens") for request in requests)
    asyncio.run(run())


@pytest.mark.parametrize("usage_source", ["cleanup", "terminal_failure"])
def test_failed_world_reconciles_reported_usage_and_latches_overrun(tmp_path, usage_source):
    overrun = {"total_input_tokens": 1_000_000, "total_output_tokens": 2_000_000, "total_tokens": 3_000_000}

    async def run():
        async with harness(tmp_path, fail="connect" if usage_source == "cleanup" else None) as (manager, stream, requests):
            original_post = manager.gateway.http.post

            async def post(path, body, on_id):
                response = await original_post(path, body, on_id)
                return {**response, "usage": overrun} if path.endswith("/cancel") else response

            manager.gateway.http.post = post
            session = development(manager)
            if usage_source == "cleanup":
                await session.attach("human", session.tokens["human"])
            else:
                await start(session)
                await stream.queue.put({"event_type": "interaction.failed", "usage": overrun})
            await finish(session)
            assert session.state == "failed" and session.terminal.is_set()
            row = manager.gateway.ledger.get(session.request_id)
            assert json.loads(row["usage"])["total_tokens"] == 3_000_000
            assert row["charged_tokens"] == 3_000_000 and row["charged_microusd"] == 36_500_000
            assert manager.gateway.ledger.budget_status()["blocks"]
            before = len(requests)
            next_session = development(manager)
            await next_session.attach("human", next_session.tokens["human"])
            await finish(next_session)
            assert next_session.error == "budget_exceeded" and len(requests) == before
    asyncio.run(run())
