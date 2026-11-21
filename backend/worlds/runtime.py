from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import secrets
import time
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from ..gateway.adapters import decode_image, event_dict, finish_cleanup, reported_usage, safe_id
from ..gateway.config import dollars
from ..gateway.contracts import (AdapterResult, CallContext, ErrorCode, GatewayError,
                                 MODELS, Service, canonical, digest)
from .contracts import Command, WorldLimits, WorldRequest
from .evidence import Recording, ReferenceStore, valid_reference

COMMAND = TypeAdapter(Command)
ROLES = ("human", "defender", "adversary")


class WorldSession:
    def __init__(self, gateway, limits, *, prompt, image, context, seconds, max_usd, provenance):
        self.gateway, self.limits = gateway, limits
        self.id = str(uuid4())
        self.context = context
        self.seconds = min(seconds, limits.max_seconds)
        rate = gateway.config.rates[Service.GENIE]
        budget = dollars(min(Decimal(max_usd), limits.max_usd))
        input_cost = rate.charge(gateway.config.input_tokens_per_call, 0)
        per_second = rate.charge(0, 5760)
        if per_second:
            # Leave one token for rounding when converting a fractional deadline.
            self.seconds = min(self.seconds, (budget - input_cost) / per_second
                               - gateway.config.cleanup_seconds - 1 / 5760)
        if self.seconds < 1:
            raise GatewayError(ErrorCode.BUDGET)
        output_bound = math.ceil(5760 * (self.seconds + gateway.config.cleanup_seconds))
        if rate.charge(gateway.config.input_tokens_per_call, output_bound) > budget:
            raise GatewayError(ErrorCode.BUDGET)
        payload = {"create": {"model": MODELS[Service.GENIE]}, "world_setup": {
            "event_type": "world_setup", "world_prompt": {"text": prompt}, "reference_image": image},
            "provenance": provenance}
        gateway._input(prompt)
        self.call = gateway._prepare(Service.GENIE, "world", payload, context, output_bound)
        self.recording = Recording(gateway.config.data_dir, self.id)
        self.provenance = provenance
        self.state = "awaiting_connection"
        self.key_owner = "human"
        self.held = {}
        self.session = None
        self.provider_id = None
        self.creation_may_be_active = False
        self.request_id = None
        self.termination = None
        self.error = None
        self.reason = None
        self.usage = None
        self.created = time.monotonic()
        self.last_frame = None
        self.last_steer = -math.inf
        self.steering_bytes = 0
        self.last_command = {}
        self.connections = {}
        self.queues = {role: asyncio.Queue(maxsize=3) for role in ROLES}
        self.ready, self.finished, self.stopping, self.terminal = (asyncio.Event() for _ in range(4))
        self.send_lock = asyncio.Lock()
        self.tokens = {role: secrets.token_urlsafe(32) for role in ROLES}
        self.token_hashes = {role: digest(token.encode()) for role, token in self.tokens.items()}
        self.recording.event("created", **provenance, seconds=self.seconds)
        self._save()
        self.task = asyncio.create_task(self._await_connection())

    def status(self):
        return {"session_id": self.id, "state": self.state, "request_id": self.request_id,
                "provider_interaction_id": self.provider_id, "frame_index": self.recording.frame_index,
                "frames_received": self.recording.frame_index + 1, "key_owner": self.key_owner,
                "text_owner": "adversary", "held_keys": sorted(self.held),
                "seconds_limit": self.seconds, "stop_reason": self.reason,
                "termination": self.termination, "error": self.error,
                "development_only": self.provenance["development_only"],
                "reference_sha256": self.provenance["reference_sha256"]}

    @property
    def termination_pending(self):
        return self.creation_may_be_active and not self.terminal.is_set()

    def _save(self):
        self.recording.summary({**self.status(), "provenance": self.provenance,
            "usage": self.usage.model_dump() if self.usage else None,
            "pause_supported": False, "pause_verification": "unsupported by documented protocol; not experimentally tested"})

    def _publish(self, item):
        for queue in self.queues.values():
            # Display delivery may skip old frames. Recording never does.
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(item)

    async def attach(self, role: str, token: str):
        expected = self.token_hashes.get(role)
        if (not expected or not secrets.compare_digest(expected, digest(token.encode()))
                or role in self.connections or self.finished.is_set() or self.stopping.is_set()
                or time.monotonic() - self.created > self.limits.attach_seconds):
            raise GatewayError(ErrorCode.AUTH)
        del self.token_hashes[role]
        self.tokens.pop(role, None)
        self.connections[role] = time.monotonic()
        self.recording.event("attached", role=role)

    async def disconnected(self, role):
        if role in self.connections:
            self.connections.pop(role)
            await self.stop("client_disconnect")

    async def stop(self, reason="stop"):
        if not self.stopping.is_set():
            self.reason = reason
            self.stopping.set()
            try:
                self.recording.event("stop_requested", reason=reason)
            except OSError:
                self.error = ErrorCode.STORAGE.value

    async def _await_connection(self):
        try:
            while "human" not in self.connections:
                if self.stopping.is_set() or time.monotonic() - self.created >= self.limits.attach_seconds:
                    self.reason = self.reason or "connection_timeout"
                    self.state = "stopped"
                    return
                await asyncio.sleep(.02)
            if self.stopping.is_set():
                self.state = "stopped"
                return
            result = await self.gateway._execute(self.call, world_executor=self,
                world_timeout=self.seconds + self.gateway.config.cleanup_seconds + 2)
            self.request_id = result.request_id
            self.state = "completed"
        except GatewayError as exc:
            self.request_id = exc.request_id
            self.error = exc.code.value
            self.state = ("termination_unconfirmed" if exc.code == ErrorCode.TERMINATION else
                          "stopped" if exc.code == ErrorCode.CANCELLED else "failed")
        except asyncio.CancelledError:
            self.state = "termination_unconfirmed" if self.termination_pending else "stopped"
            self.error = ErrorCode.TERMINATION.value if self.termination_pending else ErrorCode.CANCELLED.value
            raise
        finally:
            self.tokens.clear()
            self.token_hashes.clear()
            try:
                self._save()
            finally:
                self.finished.set()
                self._publish({"kind": "status", **self.status()})

    async def _send(self, message):
        async with asyncio.timeout(self.limits.send_seconds):
            await self.session.send(message)

    async def _release(self, key):
        # Keep uncertain held state until an explicit successful release or terminal ack.
        await self._send({"event_type": "user_action", "action": "key_up", "input": key})
        self.held.pop(key, None)
        self.recording.event("key_up", key=key)

    async def _release_all(self):
        for key in list(self.held):
            await self._release(key)

    async def command(self, raw: dict, role: str):
        if role not in self.connections or self.stopping.is_set() or self.finished.is_set():
            raise GatewayError(ErrorCode.INVALID_INPUT)
        try:
            command = COMMAND.validate_python(raw)
        except ValidationError:
            # A malformed control message ends control, even if a key is currently held.
            await self.stop("invalid_command")
            raise GatewayError(ErrorCode.INVALID_INPUT) from None
        self.connections[role] = time.monotonic()
        if command.kind == "heartbeat":
            return
        if command.kind in ("stop", "emergency_stop"):
            await self.stop(command.kind)
            return
        if not self.ready.is_set() or self.session is None:
            raise GatewayError(ErrorCode.INVALID_INPUT)
        # Prevent control flooding from starving expiry/watchdog tasks.
        now = time.monotonic()
        if now - self.last_command.get(role, -math.inf) < .02:
            await self.stop("command_rate_limit")
            raise GatewayError(ErrorCode.INVALID_INPUT)
        self.last_command[role] = now
        try:
            async with self.send_lock:
                if self.stopping.is_set():
                    raise GatewayError(ErrorCode.INVALID_INPUT)
                if command.kind == "takeover":
                    if role != "human":
                        raise GatewayError(ErrorCode.AUTH)
                    await self._release_all()
                    self.key_owner = command.owner
                    self.recording.event("takeover", owner=command.owner)
                elif command.kind == "release":
                    if role != self.key_owner and role != "human":
                        raise GatewayError(ErrorCode.AUTH)
                    await self._release_all()
                elif command.kind == "key":
                    if role != self.key_owner or command.owner != role or command.hold_ms > self.limits.max_key_ms:
                        raise GatewayError(ErrorCode.AUTH)
                    if self.last_frame is None or now - self.last_frame > self.limits.idle_seconds:
                        raise GatewayError(ErrorCode.INVALID_INPUT)
                    # Repeated key-down is forbidden; expiry cannot be extended indefinitely.
                    if command.key in self.held:
                        raise GatewayError(ErrorCode.INVALID_INPUT)
                    self.held[command.key] = now + command.hold_ms / 1000
                    self.recording.event("key_down_requested", key=command.key, owner=role, hold_ms=command.hold_ms)
                    await self._send({"event_type": "user_action", "action": "key_down", "input": command.key})
                    self.recording.event("key_down_sent", key=command.key, owner=role)
                elif command.kind == "steer":
                    if role != "adversary" or now - self.last_steer < 2:
                        raise GatewayError(ErrorCode.AUTH)
                    length = len(command.text.encode())
                    if (self.steering_bytes + length > self.limits.max_steering_bytes
                            or self.gateway.http.api_key in command.text):
                        raise GatewayError(ErrorCode.INVALID_INPUT)
                    self.steering_bytes += length
                    self.last_steer = now
                    self.recording.event("steering_requested", text=command.text, owner=role)
                    await self._send({"event_type": "input", "content": [{"type": "text", "text": command.text}]})
                    self.recording.event("steering_sent", text_sha256=digest(command.text.encode()))
        except BaseException:
            await self.stop("control_error")
            raise

    async def _receive(self):
        try:
            async for item in self.session.receive():
                event = event_dict(item)
                kind = event.get("event_type")
                if kind == "step.delta" and event.get("delta", {}).get("type") == "video":
                    data = event["delta"].get("data", "")
                    if len(data) > self.gateway.config.max_response_bytes:
                        raise GatewayError(ErrorCode.INVALID_OUTPUT)
                    decoded, size = decode_image({"mime_type": "image/jpeg", "data": data})
                    row = self.recording.frame(decoded, size)
                    self.last_frame = time.monotonic()
                    self._publish({"kind": "frame", "frame_index": row["frame_index"],
                        "received_at": row["received_at"], "mime_type": "image/jpeg", "data": data})
                    if self.recording.bytes >= self.limits.max_recording_bytes:
                        await self.stop("recording_limit")
                elif kind in ("interaction.completed", "interaction.cancelled", "interaction.failed"):
                    self.termination = kind
                    self.usage = reported_usage(event.get("usage") or event.get("interaction", {}).get("usage"))
                    self.held.clear()
                    self.terminal.set()
                    if kind == "interaction.failed" or event.get("status", {}).get("code", 0):
                        self.error = ErrorCode.UPSTREAM.value
                    await self.stop("provider_terminal")
                    return
                elif kind == "error":
                    raise GatewayError(ErrorCode.UPSTREAM)
            if not self.terminal.is_set():
                raise GatewayError(ErrorCode.TRANSPORT)
        except Exception as exc:
            self.error = (exc.code.value if isinstance(exc, GatewayError) else
                          ErrorCode.STORAGE.value if isinstance(exc, OSError) else ErrorCode.TRANSPORT.value)
            await self.stop("stream_error")

    async def _watchdog(self):
        while not self.stopping.is_set():
            now = time.monotonic()
            if any(now - seen >= self.limits.idle_seconds for seen in self.connections.values()):
                await self.stop("heartbeat_timeout")
            if now - (self.last_frame or self.connected_at) >= self.limits.frame_timeout:
                self.error = ErrorCode.TIMEOUT.value
                await self.stop("frame_timeout")
            async with self.send_lock:
                for key, expiry in list(self.held.items()):
                    if now >= expiry:
                        await self._release(key)
            await asyncio.sleep(.02)

    async def _cleanup(self):
        """Completion and fallback cancellation share one finite cleanup allowance."""
        deadline = time.monotonic() + self.gateway.config.cleanup_seconds
        try:
            if self.session and not self.terminal.is_set():
                async with asyncio.timeout(self.gateway.config.cleanup_seconds / 2):
                    async with self.send_lock:
                        await self._release_all()
                        await self.session.complete()
                        self.recording.event("complete_sent")
                    await self.terminal.wait()
        except Exception:
            pass
        if self.provider_id and not self.terminal.is_set():
            try:
                async with asyncio.timeout(max(.01, deadline - time.monotonic())):
                    raw = await self.gateway.http.post(f"/interactions/{self.provider_id}/cancel", b"{}", lambda _: None)
                    if raw.get("status") in ("completed", "cancelled", "failed"):
                        self.termination = raw["status"]
                        self.usage = reported_usage(raw.get("usage"))
                        self.terminal.set()
                        self.held.clear()
            except Exception:
                pass
        try:
            self.recording.event("termination", confirmed=self.terminal.is_set(), outcome=self.termination)
        except OSError:
            self.error = ErrorCode.STORAGE.value

    async def execute(self, call, on_id):
        payload = json.loads(call.body)
        created_maybe = False
        reader = watcher = None
        connection = None
        cancelled = None
        self.state = "connecting"
        def check_stopped():
            if self.stopping.is_set():
                raise GatewayError(ErrorCode.CANCELLED)
        try:
            async with asyncio.timeout(self.seconds):
                check_stopped()
                created_maybe = True
                self.creation_may_be_active = True
                created = await self.gateway.http.post("/interactions", canonical(payload["create"]), lambda _: None)
                self.provider_id = safe_id(created.get("id"), self.gateway.http.api_key)
                if not self.provider_id:
                    raise GatewayError(ErrorCode.TERMINATION)
                on_id(self.provider_id)
                self.recording.event("provider_created", interaction_id=self.provider_id)
                self._save()
                check_stopped()
                token = await self.gateway.http.post(f"/interactions/{self.provider_id}/auth_tokens", b"{}", lambda _: None)
                check_stopped()
                scoped_key = token.get("auth_token")
                if (not isinstance(scoped_key, str) or not scoped_key.startswith("auth_tokens/")
                        or self.gateway.http.api_key in scoped_key):
                    raise GatewayError(ErrorCode.AUTH)
                connection = self.gateway.genie.connector(scoped_key, self.provider_id, self.seconds)
                self.session = await connection.__aenter__()
                self.connected_at = time.monotonic()
                reader = asyncio.create_task(self._receive())
                check_stopped()
                await self._send(payload["world_setup"])
                self.recording.event("world_setup_sent")
                self.state = "running"
                self.ready.set()
                self._save()
                watcher = asyncio.create_task(self._watchdog())
                stop_wait = asyncio.create_task(self.stopping.wait())
                try:
                    done, _ = await asyncio.wait((watcher, stop_wait), return_when=asyncio.FIRST_COMPLETED)
                    if watcher in done:
                        await watcher
                finally:
                    stop_wait.cancel()
                    await asyncio.gather(stop_wait, return_exceptions=True)
        except TimeoutError:
            self.reason = self.reason or "duration_limit"
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                cancelled = exc
                self.error = ErrorCode.CANCELLED.value
            else:
                self.error = exc.code.value if isinstance(exc, GatewayError) else ErrorCode.TRANSPORT.value
            self.reason = self.reason or ("caller_cancelled" if cancelled is not None else "upstream_error")
            if not self.provider_id and isinstance(exc, GatewayError) and 400 <= (exc.status or 0) < 500:
                created_maybe = False
                self.creation_may_be_active = False
        finally:
            self.stopping.set()
            self.state = "stopping"
            async def close_resources():
                if watcher:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
                # Keep the sole receiver alive until completion can be acknowledged.
                try:
                    await self._cleanup()
                finally:
                    if reader:
                        reader.cancel()
                        await asyncio.gather(reader, return_exceptions=True)
                    if connection and self.session:
                        try:
                            async with asyncio.timeout(.5):
                                await connection.__aexit__(None, None, None)
                        except Exception:
                            pass
                    self.session = None
            cleanup_cancelled = await finish_cleanup(close_resources())
            if cleanup_cancelled is not None:
                cancelled = cleanup_cancelled
        if cancelled is not None:
            raise cancelled
        if created_maybe and not self.terminal.is_set():
            raise GatewayError(ErrorCode.TERMINATION)
        if self.error:
            raise GatewayError(ErrorCode(self.error))
        if self.recording.frame_index < 0:
            raise GatewayError(ErrorCode.INVALID_OUTPUT)
        self.state = "completed"
        output = {**self.status(), "provenance": self.provenance}
        return AdapterResult(output, output, self.provider_id, self.usage)


class WorldManager:
    def __init__(self, gateway, limits: WorldLimits | None = None):
        self.gateway = gateway
        self.limits = limits or WorldLimits()
        self.references = ReferenceStore(gateway.config.data_dir)
        self.sessions = {}

    def create(self, request: WorldRequest):
        record, image = self.references.load(request)
        return self._create(prompt=request.prompt, image=image, context=request.context,
            seconds=request.seconds, max_usd=request.max_usd,
            provenance={**record.model_dump(), "accepted_reference": request.accepted_reference, "development_only": False})

    def development(self, *, prompt, image, seconds=15, max_usd=Decimal("2.5")):
        data = valid_reference(image)
        return self._create(prompt=prompt, image=image, seconds=seconds, max_usd=max_usd,
            context=CallContext(episode_id="world-dev-" + str(uuid4()), prompt_id="world-spike-v1", purpose="development"),
            provenance={"development_only": True, "reference_sha256": digest(data)})

    def _create(self, **kwargs):
        if not self.gateway._key:
            raise GatewayError(ErrorCode.NOT_CONFIGURED)
        if not self.gateway.config.enabled:
            raise GatewayError(ErrorCode.DISABLED)
        if datetime.now(timezone.utc).date() > self.gateway.config.rates_valid_through:
            raise GatewayError(ErrorCode.BUDGET)
        # Completed recordings remain on disk, not in an unbounded in-memory registry.
        self.sessions = {key: value for key, value in self.sessions.items() if not value.finished.is_set()}
        if len(self.sessions) >= self.limits.max_sessions:
            raise GatewayError(ErrorCode.CONCURRENCY)
        session = WorldSession(self.gateway, self.limits, **kwargs)
        self.sessions[session.id] = session
        return session

    async def close(self):
        for session in self.sessions.values():
            await session.stop("server_shutdown")
        if self.sessions:
            await asyncio.gather(*(s.task for s in self.sessions.values()), return_exceptions=True)

    async def recover(self):
        # Only expired leases: a second worker must never cancel a live owner's session.
        for request_id in self.gateway.ledger.budget_status()["unresolved_world_requests"]:
            row = self.gateway.ledger.get(request_id)
            if row and row["lease_until"] < time.time() and row["provider_request_id"]:
                try:
                    await self.gateway.cancel_world(request_id)
                except GatewayError:
                    pass  # Remains visibly unresolved and blocks new Genie admission.

    async def watch_abandoned(self):
        while True:
            await self.recover()
            await asyncio.sleep(5)
