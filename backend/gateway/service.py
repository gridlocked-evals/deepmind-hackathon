from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import json
import math
import re
import sqlite3
import time
from uuid import uuid4

import httpx
from pydantic import BaseModel

from .adapters import GenieAdapter, HTTPAdapter, reported_usage, safe_id
from .config import GatewayConfig, backend_key
from .contracts import (MODELS, STAGES, CallContext, CallResult, ErrorCode, GatewayError, GenieCallState,
                        PreparedCall, Service, canonical, digest)
from .ledger import Ledger


def provider_schema(schema: dict) -> dict:
    """Project serving grammar; retain all stricter validation in response_model."""
    omitted = {"title", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
               "minItems", "maxItems", "minLength", "maxLength"}
    def project(value):
        if isinstance(value, dict):
            return {key: project(item) for key, item in value.items() if key not in omitted}
        if isinstance(value, list):
            return [project(item) for item in value]
        return value
    return project(schema)


class ModelGateway:
    def __init__(self, config: GatewayConfig, client: httpx.AsyncClient, *, api_key: str | None = None,
                 genie_connector=None):
        self.config = config
        self._key = api_key if api_key is not None else backend_key(config.key_profile)
        self._credential_hash = digest((self._key or "").encode())
        self.ledger = Ledger(config)
        self.http = HTTPAdapter(client, self._key or "", config)
        self.genie = GenieAdapter(self.http, genie_connector) if genie_connector else GenieAdapter(self.http)
        # Queue this process's calls; the ledger's limit still guards other workers.
        # Genie stays out: the ledger's one-world rule bounds it, and a live world must not starve judges.
        self.slots = asyncio.Semaphore(config.max_concurrency)

    def _input(self, value):
        """Inline, immutable inputs only: remote URLs could change between retries."""
        if isinstance(value, str):
            if not value.strip() or len(value.encode()) > self.config.input_tokens_per_call:
                raise GatewayError(ErrorCode.INVALID_INPUT)
            return
        if not isinstance(value, list) or not 1 <= len(value) <= 257:
            raise GatewayError(ErrorCode.INVALID_INPUT)
        text_bytes = 0
        for part in value:
            if not isinstance(part, dict):
                raise GatewayError(ErrorCode.INVALID_INPUT)
            if part.get("type") == "text" and set(part) == {"type", "text"} and isinstance(part["text"], str):
                text_bytes += len(part["text"].encode())
            elif (part.get("type") in ("image", "video") and set(part) == {"type", "mime_type", "data"}
                  and part["mime_type"] in ({"image/png", "image/jpeg"} if part["type"] == "image" else {"video/mp4", "video/webm"})):
                try:
                    if not base64.b64decode(part["data"], validate=True):
                        raise ValueError()
                except (ValueError, TypeError):
                    raise GatewayError(ErrorCode.INVALID_INPUT) from None
            else:
                raise GatewayError(ErrorCode.INVALID_INPUT)
        if text_bytes > self.config.input_tokens_per_call:
            raise GatewayError(ErrorCode.INVALID_INPUT)

    def _prepare(self, service: Service, stage: str, payload: dict, context: CallContext,
                 output_bound: int, response_model=None, *, qualify: bool = True) -> PreparedCall:
        if STAGES.get(stage) != service:
            raise GatewayError(ErrorCode.INVALID_INPUT)
        if context.purpose in ("submission", "demo") and (not context.source_id or not context.capture_id):
            raise GatewayError(ErrorCode.INVALID_INPUT)
        try:
            body = canonical(payload)
        except (TypeError, ValueError):
            raise GatewayError(ErrorCode.INVALID_INPUT) from None
        if len(body) > self.config.max_request_bytes or (self._key and (
            self._key.encode() in body or self._key.encode() in canonical(context.model_dump()))):
            raise GatewayError(ErrorCode.INVALID_INPUT)
        input_bound = self.config.input_tokens_per_call
        if service == Service.NANO_BANANA:
            input_bound = min(input_bound, self.config.max_request_tokens - output_bound)
            if input_bound <= 0:
                raise GatewayError(ErrorCode.BUDGET)
        return PreparedCall(service, stage, body, context, self.config.config_hash,
                            input_bound, output_bound, response_model, qualify)

    async def generate(self, service: Service, *, stage: str, input_value, context: CallContext,
                       response_model: type[BaseModel] | None = None, qualify: bool = True) -> CallResult:
        if type(qualify) is not bool:
            raise GatewayError(ErrorCode.INVALID_INPUT)
        if service not in (Service.HYDROGEN, Service.CARBON, Service.NANO_BANANA):
            raise GatewayError(ErrorCode.INVALID_INPUT)
        self._input(input_value)
        output_bound = (self.config.image_output_tokens_per_call if service == Service.NANO_BANANA
                        else self.config.output_tokens_per_call)
        payload = {"model": MODELS[service], "input": input_value, "store": False,
                   "generation_config": {"max_output_tokens": output_bound}}
        if service == Service.HYDROGEN and stage == "observation":
            # Preserve the policy verified against main's six-frame extraction.
            payload["generation_config"]["thinking_level"] = "low"
        if service == Service.NANO_BANANA:
            if response_model:
                raise GatewayError(ErrorCode.INVALID_INPUT)
            # The live endpoint rejects the documented delivery selector. Its
            # default returns inline image data; validate that in the adapter.
            payload["response_format"] = {"type": "image", "aspect_ratio": "16:9", "image_size": "1K"}
        elif response_model:
            payload["response_format"] = {"type": "text", "mime_type": "application/json", "schema": provider_schema(response_model.model_json_schema())}
        call = self._prepare(service, stage, payload, context, output_bound, response_model, qualify=qualify)
        return await self._execute(call)

    async def judge(self, *, stage: str, input_value, questions: dict, context: CallContext) -> CallResult:
        self._input(input_value)
        if not isinstance(questions, dict) or not 1 <= len(questions) <= 16:
            raise GatewayError(ErrorCode.INVALID_INPUT)
        for name, question in questions.items():
            if (not isinstance(name, str) or not name or len(name) > 64 or not isinstance(question, dict)
                    or question.get("type") not in ("noul", "choice", "score")
                    or not isinstance(question.get("instructions"), str) or not question["instructions"].strip()
                    or set(question) - {"type", "instructions", "criteria"}):
                raise GatewayError(ErrorCode.INVALID_INPUT)
            criteria = question.get("criteria")
            if question["type"] == "choice" and (not isinstance(criteria, dict) or not 2 <= len(criteria) <= 255
                or not all(isinstance(k, str) and k and isinstance(v, str) for k, v in criteria.items())):
                raise GatewayError(ErrorCode.INVALID_INPUT)
            if question["type"] == "score" and (not isinstance(criteria, list) or not 2 <= len(criteria) <= 255
                or not all(isinstance(item, str) and item.strip() for item in criteria)):
                raise GatewayError(ErrorCode.INVALID_INPUT)
            if question["type"] == "noul" and criteria is not None and (not isinstance(criteria, dict)
                or set(criteria) != {"true_description", "false_description"}
                or not all(isinstance(item, str) for item in criteria.values())):
                raise GatewayError(ErrorCode.INVALID_INPUT)
        payload = {"model": MODELS[Service.DECISIONS], "input": input_value, "questions": questions}
        return await self._execute(self._prepare(Service.DECISIONS, stage, payload, context, 0))

    async def probe_world(self, *, prompt: str, context: CallContext, reference_image: dict | None = None) -> CallResult:
        if context.purpose != "smoke":
            # Production creation requires accepted reference/gate provenance in #9.
            raise GatewayError(ErrorCode.INVALID_INPUT)
        self._input(prompt)
        payload = {"create": {"model": MODELS[Service.GENIE]},
                   "world_setup": {"event_type": "world_setup", "world_prompt": {"text": prompt}}}
        if reference_image is not None:
            self._input([{"type": "image", **reference_image}])
            payload["world_setup"]["reference_image"] = reference_image
        bound = math.ceil(5760 * (self.config.genie_seconds + self.config.cleanup_seconds))
        return await self._execute(self._prepare(Service.GENIE, "world", payload, context, bound))

    def _redact(self, value):
        if isinstance(value, dict):
            return {self._redact(key): self._redact(item) for key, item in value.items()
                    if key.lower() not in ("api_key", "auth_token", "authorization", "access_token", "continuation_token")}
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, str) and self._key:
            return value.replace(self._key, "[REDACTED]")
        return value

    async def _execute(self, call: PreparedCall, *, world_executor=None,
                       world_timeout: float | None = None) -> CallResult:
        call_id = str(uuid4())
        model = MODELS[call.service]
        rate = self.config.rates[call.service]
        timeout = self.config.timeout_seconds
        if call.service == Service.GENIE:
            timeout += self.config.genie_seconds + self.config.cleanup_seconds + 1
            if world_timeout is not None:
                timeout = world_timeout
        attempts = 1 if call.service == Service.GENIE else self.config.max_attempts
        world_state = (world_executor or GenieCallState()) if call.service == Service.GENIE else None
        for attempt in range(attempts):
            request_id = None
            started = time.monotonic()
            response_evidence = None
            acquired = False
            try:
                if call.service != Service.GENIE:
                    try:
                        await asyncio.wait_for(self.slots.acquire(), timeout=timeout)
                    except TimeoutError:
                        raise GatewayError(ErrorCode.CONCURRENCY) from None
                    acquired = True
                if call.service == Service.GENIE and not self.config.genie_enabled:
                    raise GatewayError(ErrorCode.DISABLED)
                if not self._key:
                    raise GatewayError(ErrorCode.NOT_CONFIGURED)
                if not self.config.enabled:
                    raise GatewayError(ErrorCode.DISABLED)
                if datetime.now(timezone.utc).date() > self.config.rates_valid_through:
                    raise GatewayError(ErrorCode.BUDGET)
                request_id = self.ledger.reserve(call, call_id, self._credential_hash, model,
                    rate.charge(call.input_token_bound, call.output_token_bound),
                    call.input_token_bound + call.output_token_bound, timeout)
                self.ledger.artifact(call.body)
                self.ledger.dispatched(request_id)
                def on_id(identifier):
                    self.ledger.provider_id(request_id, identifier)
                def on_response(raw):
                    nonlocal response_evidence
                    response_evidence = self._redact(raw)
                async with asyncio.timeout(timeout):
                    if call.service == Service.GENIE:
                        if world_executor is not None:
                            result = await world_executor.execute(call, on_id)
                        else:
                            result = await self.genie.execute(call, on_id, world_state)
                    else:
                        result = await self.http.execute(call, on_id, on_response)
                evidence = self._redact(result.evidence)
                result_hash = self.ledger.artifact(canonical(evidence))
                qualifying = bool(call.qualify and call.response_model and isinstance(result.output, dict) and result.output
                                  and call.stage in ("observation", "compilation"))
                self.ledger.finish(request_id, outcome="success", latency_ms=(time.monotonic() - started) * 1000,
                    result_hash=result_hash, usage=result.usage,
                    microusd=rate.usage_charge(result.usage) if result.usage else None, qualifying=qualifying)
                return CallResult(request_id, result.provider_request_id, model, call.input_hash, result_hash,
                                  self._redact(result.output), result.usage,
                                  "reported_usage_upper_bound" if result.usage else "reservation_retained")
            except (GatewayError, TimeoutError, asyncio.CancelledError, OSError, sqlite3.Error) as exc:
                if isinstance(exc, asyncio.CancelledError) and request_id is None:
                    # Waiting for admission is not a model attempt. Preserve
                    # cancellation without attributing it to a new ledger row.
                    raise
                if isinstance(exc, GatewayError):
                    error = exc
                elif isinstance(exc, TimeoutError):
                    error = GatewayError(ErrorCode.TIMEOUT)
                elif isinstance(exc, asyncio.CancelledError):
                    error = GatewayError(ErrorCode.CANCELLED)
                else:
                    error = GatewayError(ErrorCode.STORAGE)
                if world_state is not None and world_state.termination_pending:
                    # Preserve the unresolved-world admission latch even if caller
                    # cancellation or a storage failure interrupts finalization.
                    error = GatewayError(ErrorCode.TERMINATION)
                try:
                    if request_id:
                        evidence = response_evidence or {"error": error.public()}
                        usage = reported_usage(response_evidence.get("usage")) if response_evidence else None
                        if world_state is not None:
                            usage = world_state.usage
                            evidence = {**evidence, "termination": world_state.termination,
                                        "usage": usage.model_dump() if usage else None}
                        result_hash = self.ledger.artifact(canonical(evidence))
                        self.ledger.finish(request_id, outcome=error.code.value,
                            latency_ms=(time.monotonic() - started) * 1000, result_hash=result_hash,
                            usage=usage, microusd=rate.usage_charge(usage) if usage else None)
                    else:
                        request_id = self.ledger.reject(call, call_id, self._credential_hash, model, error.code)
                except (OSError, sqlite3.Error, GatewayError):
                    # A pending reservation remains charged if recording the result fails.
                    raise GatewayError(ErrorCode.STORAGE, request_id=request_id) from None
                error.request_id = request_id
                if isinstance(exc, asyncio.CancelledError):
                    # Preserve cancellation while letting callers link their failed attempt to this real ledger row.
                    if request_id is not None:
                        exc.request_id = request_id
                    raise
                if not error.retryable or attempt + 1 >= attempts:
                    raise error from None
                await asyncio.sleep(min(self.config.retry_delay_seconds, max(error.retry_after, .25 * 2 ** attempt)))
            finally:
                if acquired:
                    self.slots.release()
        raise AssertionError("unreachable")

    def status(self) -> dict:
        now = time.time()
        services = []
        for service, model in MODELS.items():
            last = self.ledger.latest(service.value, self._credential_hash, self.config.config_hash)
            if service == Service.GENIE and not self.config.genie_enabled:
                state, reason = "unavailable", ErrorCode.DISABLED.value
            elif not self._key:
                state, reason = "unavailable", ErrorCode.NOT_CONFIGURED.value
            elif not self.config.enabled:
                state, reason = "configured", ErrorCode.DISABLED.value
            elif not last:
                state, reason = "unverified", None
            elif last["outcome"] == "success":
                state = "connected" if now - last["finished_at"] <= self.config.stale_after_seconds else "stale"
                reason = None
            elif last["outcome"] == "pending":
                state, reason = "unverified", "request_in_flight_or_interrupted"
            else:
                state, reason = "unavailable", last["outcome"]
            services.append({"service": service.value, "model_id": model, "state": state, "reason": reason,
                "optional": service == Service.GENIE,
                "enabled": self.config.enabled and (service != Service.GENIE or self.config.genie_enabled),
                "configured": bool(self._key), "last_request_id": last["request_id"] if last else None,
                "last_provider_request_id": last["provider_request_id"] if last else None,
                "last_latency_ms": last["latency_ms"] if last else None,
                "verified_at": last["finished_at"] if last and last["outcome"] == "success" else None,
                "usage_reported": bool(last and last["usage"]),
                "project_quota": "unmeasured; successful probes establish access, not maximum throughput"})
        return {"schema_version": 1, "services": services, "budgets": self.ledger.budget_status(),
                "key_profile": self.config.key_profile,
                "config_hash": self.config.config_hash,
                "rates_expired": datetime.now(timezone.utc).date() > self.config.rates_valid_through}

    def require_submission_models(self, *, episode_id: str, source_id: str, capture_id: str) -> dict:
        report = self.ledger.submission_models(episode_id, source_id, capture_id)
        if not report["ready"]:
            raise GatewayError(ErrorCode.INVALID_INPUT)
        return report

    def accept_artifact(self, result: CallResult, *, artifact_id: str, artifact_sha256: str, qualifying: bool = True) -> str:
        """Qualify only after the caller durably accepts its domain artifact.

        The caller owns domain validation and storage. This records an immutable
        binding to its envelope hash, without repeating model calls or billing.
        """
        if (type(qualifying) is not bool or not isinstance(artifact_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", artifact_id)
                or not isinstance(artifact_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", artifact_sha256)):
            raise GatewayError(ErrorCode.INVALID_INPUT)
        return self.ledger.accept_artifact(result, artifact_id, artifact_sha256, qualifying=qualifying)

    async def cancel_world(self, request_id: str) -> dict:
        """Recover a known interrupted development session without admitting new spend."""
        row = self.ledger.get(request_id)
        if not self._key:
            raise GatewayError(ErrorCode.NOT_CONFIGURED)
        if (not row or row["service"] != Service.GENIE.value or not row["dispatched"]
                or row["credential_hash"] != self._credential_hash
                or row["outcome"] not in ("pending", ErrorCode.TERMINATION.value)
                or not safe_id(row["provider_request_id"], self._key)):
            raise GatewayError(ErrorCode.INVALID_INPUT)
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.config.cleanup_seconds):
                raw = await self.http.post(f"/interactions/{row['provider_request_id']}/cancel", b"{}", lambda _: None)
            if raw.get("status") not in ("completed", "cancelled", "failed"):
                raise GatewayError(ErrorCode.TERMINATION, request_id=request_id)
        except (GatewayError, TimeoutError):
            raise GatewayError(ErrorCode.TERMINATION, request_id=request_id) from None
        usage = reported_usage(raw.get("usage"))
        evidence = {"previous_result_hash": row["result_hash"], "reconciliation": self._redact(raw)}
        fingerprint = self.ledger.artifact(canonical(evidence))
        self.ledger.finish(request_id, outcome="cancelled", result_hash=fingerprint,
            latency_ms=(row["latency_ms"] or 0) + (time.monotonic() - started) * 1000,
            usage=usage, microusd=self.config.rates[Service.GENIE].usage_charge(usage) if usage else None)
        return {"request_id": request_id, "termination": raw["status"], "result_hash": fingerprint}
