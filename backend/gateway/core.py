from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable
from uuid import uuid4

import httpx

MODELS = {
    "hydrogen": "gemini-4-hydrogen",
    "carbon": "gemini-4-carbon",
    "nano_banana": "gemini-nano-banana-2.1",
    "decisions": "gemini-decision-1",
    "genie": "genie-4",
}
_MESSAGES = {
    "unconfigured": "The selected Gemini credential is not configured.",
    "unavailable": "This service is unavailable for the selected project.",
    "quota": "The provider rejected the request because of quota or rate limits.",
    "timeout": "The model request exceeded its time limit.",
    "transport": "The provider could not complete the request.",
    "invalid_input": "The model request or its provenance is invalid.",
    "invalid_output": "The provider returned an invalid or incomplete result.",
    "budget": "The configured usage budget does not admit this request.",
    "busy": "The concurrency limit is occupied; try again after current work finishes.",
    "sdk_unavailable": "The installed Google GenAI SDK does not support Genie sessions.",
    "termination_unconfirmed": "Genie termination could not be confirmed; inspect the recorded interaction immediately.",
    "cancelled": "The caller cancelled the request; its reservation is retained.",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class GatewayError(Exception):
    def __init__(self, code: str, *, retryable: bool = False, trace_id: str | None = None):
        self.code, self.retryable, self.trace_id = code, retryable, trace_id
        super().__init__(_MESSAGES[code])

    def public(self) -> dict:
        return {"code": self.code, "message": str(self), "retryable": self.retryable, "traceId": self.trace_id}


@dataclass(frozen=True)
class GatewayConfig:
    api_key: str = field(default="", repr=False)
    key_profile: str = "primary"
    ledger_path: Path = Path("data/gateway.sqlite3")
    concurrency: int = 2
    retries: int = 1
    timeout_seconds: float = 30
    stale_seconds: float = 300
    total_usd: float = 20
    demo_reserve_usd: float = 5
    stage_usd: float = 5
    stage_tokens: int = 100_000
    max_request_tokens: int = 16_384
    max_request_usd: float = 4

    def __post_init__(self):
        values = (self.timeout_seconds, self.stale_seconds, self.total_usd, self.stage_usd, self.max_request_usd)
        if (self.key_profile not in {"primary", "workshop"} or not 1 <= self.concurrency <= 16
                or not 0 <= self.retries <= 3 or any(not math.isfinite(v) or v <= 0 for v in values)
                or not math.isfinite(self.demo_reserve_usd) or not 0 <= self.demo_reserve_usd <= self.total_usd
                or self.stage_tokens < 1 or self.max_request_tokens < 1):
            raise ValueError("Invalid gateway limits or key profile")

    @classmethod
    def from_env(cls) -> GatewayConfig:
        from ..config import ROOT
        profile = os.getenv("GEMINI_KEY_PROFILE", "primary")
        ledger = Path(os.getenv("MODEL_LEDGER_PATH", "data/gateway.sqlite3"))
        return cls(
            api_key=os.getenv("WORKSHOP_API_KEY" if profile == "workshop" else "GEMINI_API_KEY", "").strip(),
            key_profile=profile,
            ledger_path=ledger if ledger.is_absolute() else ROOT / ledger,
            concurrency=int(os.getenv("MODEL_MAX_CONCURRENCY", "2")),
            retries=int(os.getenv("MODEL_MAX_RETRIES", "1")),
            timeout_seconds=float(os.getenv("MODEL_TIMEOUT_SECONDS", "30")),
            total_usd=float(os.getenv("MODEL_TOTAL_BUDGET_USD", "20")),
            demo_reserve_usd=float(os.getenv("MODEL_DEMO_RESERVE_USD", "5")),
            stage_usd=float(os.getenv("MODEL_STAGE_BUDGET_USD", "5")),
            stage_tokens=int(os.getenv("MODEL_STAGE_TOKEN_BUDGET", "100000")),
            max_request_tokens=int(os.getenv("MODEL_MAX_REQUEST_TOKENS", "16384")),
            max_request_usd=float(os.getenv("MODEL_MAX_REQUEST_USD", "4")),
        )


@dataclass
class GatewayResult:
    output: dict[str, Any]
    trace: dict[str, Any]
    metadata: dict[str, Any]


class Ledger:
    """Reserve before dispatch; failures/unknown usage never release the reservation.

    SQLite transactions preserve budget admission across restarts/processes. Dollar
    values are caller-declared upper-bound reservations, not measured invoices.
    """
    def __init__(self, config: GatewayConfig):
        self.config = config
        config.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(config.ledger_path, timeout=5)
        self.db.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, service TEXT, profile TEXT, tokens INTEGER, usd REAL, data TEXT)")
        self.db.commit()

    def reserve(self, record: dict, tokens: int, usd: float, demo: bool):
        c = self.config
        self.db.execute("BEGIN IMMEDIATE")
        try:
            total = self.db.execute("SELECT COALESCE(SUM(usd),0) FROM attempts").fetchone()[0]
            stage = self.db.execute("SELECT COALESCE(SUM(tokens),0), COALESCE(SUM(usd),0) FROM attempts WHERE service=?", (record["service"],)).fetchone()
            limit = c.total_usd if demo else c.total_usd - c.demo_reserve_usd
            if total + usd > limit + 1e-9 or stage[0] + tokens > c.stage_tokens or stage[1] + usd > c.stage_usd + 1e-9:
                raise GatewayError("budget")
            self.db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?)", (record["traceId"], record["service"], c.key_profile, tokens, usd, json.dumps(record)))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def finish(self, record: dict):
        actual = record.get("totalTokens")
        # Never discount reservations; overages reduce the next request's headroom.
        with self.db:
            self.db.execute("UPDATE attempts SET data=?, tokens=MAX(tokens,?) WHERE id=?", (json.dumps(record), actual or 0, record["traceId"]))

    def records(self) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT data FROM attempts ORDER BY rowid")]

    def summary(self) -> dict:
        row = self.db.execute("SELECT COALESCE(SUM(tokens),0), COALESCE(SUM(usd),0), COUNT(*) FROM attempts").fetchone()
        return {"reservedTokens": row[0], "reservedUsd": row[1], "attempts": row[2], "actualCostUsd": None,
                "totalLimitUsd": self.config.total_usd, "demoReserveUsd": self.config.demo_reserve_usd}


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", value) is not None


def _artifact_identifier(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,95}", value) is not None


def _inline_only(value: Any):
    if isinstance(value, dict):
        if any(k in value for k in ("uri", "url", "file_uri", "fileUri", "previous_interaction_id", "tools", "background", "stream")):
            raise GatewayError("invalid_input")
        for child in value.values():
            _inline_only(child)
    elif isinstance(value, list):
        for child in value:
            _inline_only(child)


class ModelGateway:
    def __init__(self, config: GatewayConfig, *, client: httpx.AsyncClient | None = None, genie_client_factory: Callable | None = None):
        self.config = config
        self.ledger = Ledger(config)
        self._slots = asyncio.Semaphore(config.concurrency)
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=config.timeout_seconds, follow_redirects=False)
        self._genie_client_factory = genie_client_factory
        self._credential_digest = hashlib.sha256(config.api_key.encode()).hexdigest()

    @classmethod
    def from_env(cls):
        return cls(GatewayConfig.from_env())

    async def close(self):
        if self._owns_client:
            await self.client.aclose()
        self.ledger.db.close()

    def status(self) -> dict:
        records = self.ledger.records()
        services = []
        for service, model in MODELS.items():
            recent = next((r for r in reversed(records) if r["service"] == service and r["profile"] == self.config.key_profile and r.get("credentialDigest") == self._credential_digest), None)
            state = "unavailable" if not self.config.api_key else "configured"
            if self.config.api_key and recent:
                if recent["outcome"] == "success":
                    age = time.time() - datetime.fromisoformat(recent["completedAt"]).timestamp()
                    state = "connected" if age <= self.config.stale_seconds else "stale"
                else:
                    state = "unverified" if recent["outcome"] == "unknown" else "unavailable"
            services.append({"service": service, "modelId": model, "status": state,
                             "configured": bool(self.config.api_key), "checkedAt": recent.get("completedAt") if recent else None,
                             "error": recent.get("errorCode") if recent else ("unconfigured" if not self.config.api_key else None)})
        return {"keyProfile": self.config.key_profile, "services": services, "budget": self.ledger.summary(),
                "limits": {"concurrency": self.config.concurrency, "retries": self.config.retries, "timeoutSeconds": self.config.timeout_seconds},
                "quotaMeasured": False}

    async def run(self, service: str, payload: dict, *, prompt_id: str, config_id: str,
                  reserve_tokens: int, reserve_usd: float, demo: bool = False,
                  source_id: str | None = None, capture_id: str | None = None,
                  original_media_sha256: tuple[str, ...] = ()) -> GatewayResult:
        """Invoke a stage against frozen inline bytes. Never captures fresh media.

        reserve_* must bound the intended request; usage exceeding the reservation
        is retained and blocks subsequent admissions. Provider billing is not exact.
        """
        c = self.config
        if (service not in MODELS or not isinstance(payload, dict) or not all(_artifact_identifier(v) for v in (prompt_id, config_id))
                or any(v is not None and not _artifact_identifier(v) for v in (source_id, capture_id))
                or any(not isinstance(h, str) or not re.fullmatch(r"[a-f0-9]{64}", h) for h in original_media_sha256)
                or type(reserve_tokens) is not int or not 1 <= reserve_tokens <= c.max_request_tokens
                or not isinstance(reserve_usd, (int, float)) or not math.isfinite(reserve_usd) or not 0 < reserve_usd <= c.max_request_usd):
            raise GatewayError("invalid_input")
        _inline_only(payload)
        body = dict(payload)
        if service != "genie":
            body["model"] = MODELS[service]
        if service not in {"genie", "decisions"}:
            body["store"] = False
            if not isinstance(body.get("generation_config", {}), dict):
                raise GatewayError("invalid_input")
            generation = dict(body.get("generation_config", {}))
            requested_output = generation.get("max_output_tokens", min(2048, reserve_tokens))
            if type(requested_output) is not int or not 1 <= requested_output <= reserve_tokens:
                raise GatewayError("invalid_input")
            generation["max_output_tokens"] = requested_output
            body["generation_config"] = generation
        try:
            immutable = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError):
            raise GatewayError("invalid_input") from None
        if len(immutable) > 12 * 1024 * 1024:
            raise GatewayError("invalid_input")
        if not c.api_key:
            raise GatewayError("unconfigured")
        if service == "genie" and any(r.get("errorCode") == "termination_unconfirmed" for r in self.ledger.records()):
            raise GatewayError("termination_unconfirmed")
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=0.05)
        except TimeoutError:
            raise GatewayError("busy") from None
        try:
            attempts = 1 if service == "genie" else c.retries + 1
            for attempt in range(attempts):
                record = {"traceId": f"trace-{uuid4()}", "service": service, "profile": c.key_profile,
                          "modelId": MODELS[service], "requestId": f"request-{uuid4()}", "providerRequestId": None, "promptId": prompt_id, "configId": config_id,
                          "credentialDigest": self._credential_digest,
                          "inputSha256": hashlib.sha256(immutable).hexdigest(), "originalMediaSha256": list(original_media_sha256),
                          "sourceId": source_id, "captureId": capture_id, "startedAt": utc_now(), "completedAt": None,
                          "outcome": "unknown", "attempt": attempt + 1, "inputTokens": None, "outputTokens": None,
                          "totalTokens": None, "reservedTokens": reserve_tokens, "reservedUsd": reserve_usd, "actualCostUsd": None}
                self.ledger.reserve(record, reserve_tokens, reserve_usd, demo)
                started = time.monotonic()
                try:
                    # Genie has its own deadline so cancellation cannot skip remote completion.
                    if service == "genie":
                        output, request_id = await self._genie(immutable, record)
                    else:
                        async with asyncio.timeout(c.timeout_seconds):
                            output, request_id = await self._http(service, immutable)
                    self._validate_output(service, output, body)
                    usage = output.get("usage", {})
                    for key, wire in (("inputTokens", "total_input_tokens"), ("outputTokens", "total_output_tokens"), ("totalTokens", "total_tokens")):
                        value = usage.get(wire)
                        record[key] = value if type(value) is int and value >= 0 else None
                    if record["totalTokens"] is None and record["inputTokens"] is not None and record["outputTokens"] is not None:
                        record["totalTokens"] = record["inputTokens"] + record["outputTokens"]
                    record.update(outcome="success", providerRequestId=request_id,
                                  outputSha256=hashlib.sha256(json.dumps(output, sort_keys=True).encode()).hexdigest())
                    if record["totalTokens"] is not None and record["totalTokens"] > reserve_tokens:
                        record["reservationExceeded"] = True
                    self._finish(record, started)
                    trace = {key: record[key] for key in ("traceId", "requestId", "providerRequestId", "modelId", "promptId", "configId", "inputSha256", "startedAt", "completedAt", "outcome", "inputTokens", "outputTokens")}
                    from ..contracts import ModelTrace
                    ModelTrace.model_validate(trace)
                    return GatewayResult(output, trace, {key: value for key, value in record.items() if key != "credentialDigest"})
                except asyncio.CancelledError:
                    record.update(errorCode="cancelled")
                    self._finish(record, started)
                    raise
                except Exception as exc:
                    if isinstance(exc, GatewayError):
                        error = exc
                    elif isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                        error = GatewayError("timeout", retryable=True)
                    elif isinstance(exc, httpx.HTTPError):
                        error = GatewayError("transport", retryable=True)
                    else:
                        error = GatewayError("invalid_output")
                    record.update(outcome="error", errorCode=error.code)
                    self._finish(record, started)
                    error.trace_id = record["traceId"]
                    if not error.retryable or attempt + 1 == attempts:
                        raise error from None
                    await asyncio.sleep(min(0.25 * 2 ** attempt, 2))
            raise AssertionError("unreachable")
        finally:
            self._slots.release()

    def _finish(self, record, started):
        record.update(completedAt=utc_now(), latencyMs=round((time.monotonic() - started) * 1000, 2))
        self.ledger.finish(record)

    async def _http(self, service: str, body: bytes) -> tuple[dict, str | None]:
        route = "decisions" if service == "decisions" else "interactions"
        async with self.client.stream("POST", f"https://generativelanguage.googleapis.com/v1beta/{route}", content=body,
                                      headers={"x-goog-api-key": self.config.api_key, "Content-Type": "application/json", "Api-Revision": "2026-05-20"}) as response:
            if response.status_code == 429:
                raise GatewayError("quota", retryable=True)
            if response.status_code in {401, 403, 404}:
                raise GatewayError("unavailable")
            if response.status_code >= 500:
                raise GatewayError("transport", retryable=True)
            if response.status_code >= 400 or response.is_redirect:
                raise GatewayError("invalid_input")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 24 * 1024 * 1024:
                    raise GatewayError("invalid_output")
            output = json.loads(data)
            if not isinstance(output, dict):
                raise GatewayError("invalid_output")
            request_id = output.get("id") or response.headers.get("x-request-id") or response.headers.get("x-goog-request-id")
            return output, request_id if _identifier(request_id) and self.config.api_key not in request_id else None

    @staticmethod
    def _validate_output(service: str, output: dict, request: dict):
        if service == "decisions":
            answers = output.get("answers")
            questions = request.get("questions")
            if not isinstance(answers, dict) or not isinstance(questions, dict) or not questions or answers.keys() != questions.keys():
                raise GatewayError("invalid_output")
            for key, question in questions.items():
                answer = answers[key]
                if not isinstance(answer, dict) or answer.get("type") != question.get("type"):
                    raise GatewayError("invalid_output")
                confidence = answer.get("confidence")
                if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
                    raise GatewayError("invalid_output")
                if answer["type"] == "noul":
                    value = answer.get("noul")
                    if type(value) not in (int, float) or not 0.01 <= value <= 0.99:
                        raise GatewayError("invalid_output")
                elif answer["type"] in {"choice", "score"}:
                    probabilities = answer.get("probabilities")
                    if not isinstance(probabilities, dict) or not probabilities or any(type(p) not in (int, float) or not 0 <= p <= 1 for p in probabilities.values()) or abs(sum(probabilities.values()) - 1) > .02:
                        raise GatewayError("invalid_output")
                    criteria = question.get("criteria")
                    if answer["type"] == "choice":
                        if not isinstance(criteria, dict) or probabilities.keys() != criteria.keys() or answer.get("choice") not in criteria:
                            raise GatewayError("invalid_output")
                        if probabilities[answer["choice"]] < max(probabilities.values()) - 1e-6:
                            raise GatewayError("invalid_output")
                    else:
                        score = answer.get("score")
                        if not isinstance(criteria, list) or len(criteria) < 2 or probabilities.keys() != {str(i) for i in range(len(criteria))} or type(score) not in (int, float) or not 0 <= score <= len(criteria) - 1:
                            raise GatewayError("invalid_output")
                        if answer.get("legend") != {str(i): text for i, text in enumerate(criteria)}:
                            raise GatewayError("invalid_output")
                        expected_score = sum(int(level) * probability for level, probability in probabilities.items())
                        if abs(score - expected_score) > .02 * len(criteria):
                            raise GatewayError("invalid_output")
                else:
                    raise GatewayError("invalid_output")
        elif service == "genie":
            if not output.get("completed") or not output.get("frameSha256"):
                raise GatewayError("invalid_output")
        else:
            if output.get("status") != "completed":
                raise GatewayError("invalid_output")
            blocks = [block for step in output.get("steps", []) if step.get("type") == "model_output" for block in step.get("content", [])]
            desired = "image" if service == "nano_banana" else "text"
            selected = [block for block in blocks if block.get("type") == desired and block.get("data" if desired == "image" else "text")]
            if not selected:
                raise GatewayError("invalid_output")
            if desired == "image":
                for block in selected:
                    media = base64.b64decode(block["data"], validate=True)
                    if block.get("mime_type") not in {"image/png", "image/jpeg", "image/webp"} or not media:
                        raise GatewayError("invalid_output")

    async def _genie(self, body: bytes, record: dict) -> tuple[dict, str]:
        from .genie import smoke_genie
        return await smoke_genie(self.config, json.loads(body), record, self.ledger.finish, self._genie_client_factory)
