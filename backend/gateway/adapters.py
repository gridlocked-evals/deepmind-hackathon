from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from io import BytesIO
import json
import math
import re
from typing import Callable

import httpx
from PIL import Image
from pydantic import ValidationError

from .config import GatewayConfig
from .contracts import MODELS, AdapterResult, ErrorCode, GatewayError, PreparedCall, Service, Usage, canonical

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def reported_usage(raw: object) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    try:
        return Usage(input_tokens=raw["total_input_tokens"], output_tokens=raw["total_output_tokens"],
                     thought_tokens=raw.get("total_thought_tokens", 0), total_tokens=raw["total_tokens"])
    except (KeyError, ValidationError):
        return None


def safe_id(value: object, secret: str) -> str | None:
    return value if (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value)
                     and secret not in value) else None


def decode_image(part: dict) -> tuple[bytes, tuple[int, int]]:
    try:
        if part.get("mime_type") not in ("image/png", "image/jpeg"):
            raise ValueError()
        data = base64.b64decode(part["data"], validate=True)
        with Image.open(BytesIO(data)) as image:
            size = image.size
            if image.format not in ("PNG", "JPEG") or max(size) > 8192:
                raise ValueError()
            image.verify()
        return data, size
    except (KeyError, ValueError, TypeError, OSError, Image.DecompressionBombError):
        raise GatewayError(ErrorCode.INVALID_OUTPUT) from None


def validate_answers(raw: dict, questions: dict) -> dict:
    answers = raw.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise GatewayError(ErrorCode.INVALID_OUTPUT)
    def probability(value):
        return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
    for name, answer in answers.items():
        question = questions[name]
        if not isinstance(answer, dict) or answer.get("type") != question["type"] or not probability(answer.get("confidence")):
            raise GatewayError(ErrorCode.INVALID_OUTPUT)
        if question["type"] == "noul":
            if not probability(answer.get("noul")) or not .01 <= answer["noul"] <= .99:
                raise GatewayError(ErrorCode.INVALID_OUTPUT)
        else:
            probabilities = answer.get("probabilities")
            expected = set(question["criteria"]) if question["type"] == "choice" else {str(i) for i in range(len(question["criteria"]))}
            if (not isinstance(probabilities, dict) or set(probabilities) != expected
                    or not all(probability(p) for p in probabilities.values())
                    or not math.isclose(sum(probabilities.values()), 1, abs_tol=.02)):
                raise GatewayError(ErrorCode.INVALID_OUTPUT)
            if question["type"] == "choice":
                if (answer.get("choice") not in probabilities
                        or probabilities[answer["choice"]] < max(probabilities.values()) - .02):
                    raise GatewayError(ErrorCode.INVALID_OUTPUT)
            else:
                score = answer.get("score")
                legend = {str(i): value for i, value in enumerate(question["criteria"])}
                if (type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= len(legend) - 1
                        or answer.get("legend") != legend
                        or not math.isclose(score, sum(int(k) * p for k, p in probabilities.items()), abs_tol=.05)):
                    raise GatewayError(ErrorCode.INVALID_OUTPUT)
    return answers


class HTTPAdapter:
    def __init__(self, client: httpx.AsyncClient, api_key: str, config: GatewayConfig):
        self.client, self.api_key, self.config = client, api_key, config

    async def post(self, path: str, body: bytes, on_id: Callable[[str], None]) -> dict:
        try:
            async with self.client.stream(
                "POST", BASE_URL + path, content=body, follow_redirects=False,
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                timeout=self.config.timeout_seconds,
            ) as response:
                identifier = safe_id(response.headers.get("x-request-id") or response.headers.get("x-goog-request-id"), self.api_key)
                if identifier:
                    on_id(identifier)
                status = response.status_code
                if status >= 300:
                    code = ErrorCode.AUTH if status in (401, 403) else ErrorCode.QUOTA if status == 429 else ErrorCode.UPSTREAM
                    try:
                        delay = float(response.headers.get("retry-after", "0"))
                        delay = delay if math.isfinite(delay) else 0
                    except ValueError:
                        delay = 0
                    raise GatewayError(code, retryable=status in (429, 502, 503, 504), retry_after=max(0, delay), status=status)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > self.config.max_response_bytes:
                        raise GatewayError(ErrorCode.INVALID_OUTPUT)
            def invalid_constant(_):
                raise ValueError()
            raw = json.loads(data, parse_constant=invalid_constant)
            if not isinstance(raw, dict):
                raise GatewayError(ErrorCode.INVALID_OUTPUT)
            identifier = safe_id(raw.get("id"), self.api_key) or identifier
            if identifier:
                on_id(identifier)
            return raw
        except httpx.TimeoutException:
            raise GatewayError(ErrorCode.TIMEOUT) from None
        except httpx.HTTPError:
            raise GatewayError(ErrorCode.TRANSPORT) from None
        except (ValueError, UnicodeError):
            raise GatewayError(ErrorCode.INVALID_OUTPUT) from None

    async def execute(self, call: PreparedCall, on_id: Callable[[str], None], on_response=lambda _: None) -> AdapterResult:
        identifiers: list[str] = []
        def remember(identifier):
            identifiers.append(identifier)
            on_id(identifier)
        path = "/decisions" if call.service == Service.DECISIONS else "/interactions"
        raw = await self.post(path, call.body, remember)
        on_response(raw)
        if raw.get("model", MODELS[call.service]) != MODELS[call.service]:
            raise GatewayError(ErrorCode.INVALID_OUTPUT)
        usage = reported_usage(raw.get("usage"))
        if call.service == Service.DECISIONS:
            output = validate_answers(raw, json.loads(call.body)["questions"])
        else:
            if raw.get("status") != "completed":
                raise GatewayError(ErrorCode.INVALID_OUTPUT)
            steps = raw.get("steps", [])
            if not isinstance(steps, list):
                raise GatewayError(ErrorCode.INVALID_OUTPUT)
            model_outputs = [step for step in steps if isinstance(step, dict) and step.get("type") == "model_output"]
            content = model_outputs[-1].get("content", []) if model_outputs else []
            if not isinstance(content, list) or any(not isinstance(part, dict) for part in content):
                raise GatewayError(ErrorCode.INVALID_OUTPUT)
            if call.service == Service.NANO_BANANA:
                images = [part for part in content if part.get("type") == "image"]
                if len(images) != 1:
                    raise GatewayError(ErrorCode.INVALID_OUTPUT)
                _, size = decode_image(images[0])
                output = {**images[0], "width": size[0], "height": size[1]}
            else:
                text = "".join(part["text"] for part in content if part.get("type") == "text" and isinstance(part.get("text"), str))
                if not text.strip():
                    raise GatewayError(ErrorCode.INVALID_OUTPUT)
                try:
                    output = call.response_model.model_validate_json(text).model_dump(mode="json") if call.response_model else text
                except ValidationError:
                    raise GatewayError(ErrorCode.INVALID_OUTPUT) from None
        # Keep validated output and usage. No request headers, error bodies, or
        # session auth material enter evidence or returned objects.
        evidence = {"output": output, "usage": raw.get("usage"), "protocol": path[1:]}
        return AdapterResult(output, evidence, identifiers[-1] if identifiers else None, usage)


@asynccontextmanager
async def sdk_connection(api_key: str, interaction_id: str, timeout: float):
    from google import genai
    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta", "retry_options": {"attempts": 1}})
    try:
        async with client.aio.interactions.connect(interaction_id, api_version="v1beta", timeout=timeout) as session:
            yield session
    finally:
        await client.aio.aclose()
        client.close()


def event_dict(event) -> dict:
    return event if isinstance(event, dict) else event.model_dump(mode="json", exclude_none=True)


class GenieAdapter:
    """A bounded development probe, not the playable-world runtime from issue #9."""

    def __init__(self, http: HTTPAdapter, connector=sdk_connection):
        self.http, self.config, self.connector = http, http.config, connector

    async def execute(self, call: PreparedCall, on_id: Callable[[str], None]) -> AdapterResult:
        payload = json.loads(call.body)
        identifier = None
        confirmed = False
        error: BaseException | None = None
        frames = 0
        first_frame = None
        usage = None
        terminal = None
        create_started = False
        try:
            create_started = True
            created = await self.http.post("/interactions", canonical(payload["create"]), lambda _: None)
            identifier = safe_id(created.get("id"), self.http.api_key)
            if not identifier:
                raise GatewayError(ErrorCode.TERMINATION)
            on_id(identifier)
            async with asyncio.timeout(self.config.genie_seconds):
                async with self.connector(self.http.api_key, identifier, self.config.genie_seconds) as session:
                    await session.send(payload["world_setup"])
                    async for item in session.receive():
                        event = event_dict(item)
                        kind = event.get("event_type")
                        if kind == "step.delta" and event.get("delta", {}).get("type") == "video":
                            delta = event["delta"]
                            if len(delta.get("data", "")) > self.config.max_response_bytes:
                                raise GatewayError(ErrorCode.INVALID_OUTPUT)
                            decode_image({"data": delta.get("data"), "mime_type": "image/jpeg"})
                            frames += 1
                            if frames == 1:
                                first_frame = {"type": "image", "mime_type": "image/jpeg", "data": delta["data"]}
                                # A short action with key release independent of frame delivery.
                                try:
                                    await session.send({"event_type": "user_action", "action": "key_down", "input": "key_w"})
                                    await asyncio.sleep(.1)
                                finally:
                                    await session.send({"event_type": "user_action", "action": "key_up", "input": "key_w"})
                                await session.complete()
                        elif kind == "interaction.completed":
                            confirmed = True
                            terminal = kind
                            usage = reported_usage(event.get("usage") or event.get("interaction", {}).get("usage"))
                            if event.get("status", {}).get("code", 0) != 0:
                                raise GatewayError(ErrorCode.UPSTREAM)
                            break
                        elif kind in ("error", "interaction.failed", "interaction.cancelled"):
                            raise GatewayError(ErrorCode.UPSTREAM)
            if not confirmed or not frames:
                raise GatewayError(ErrorCode.INVALID_OUTPUT)
        except BaseException as exc:
            error = exc
            # An explicit rejected (4xx) create cannot have opened a session.
            if not identifier and isinstance(exc, GatewayError) and 400 <= (exc.status or 0) < 500:
                create_started = False
        finally:
            if identifier and not confirmed:
                # HTTP cancellation works even when WebSocket connection/setup fails.
                # This bypasses admission budgets: cleanup must always remain possible.
                try:
                    async with asyncio.timeout(self.config.cleanup_seconds):
                        cancelled = await self.http.post(f"/interactions/{identifier}/cancel", b"{}", lambda _: None)
                        confirmed = cancelled.get("status") in ("completed", "cancelled", "failed")
                        terminal = cancelled.get("status")
                        usage = reported_usage(cancelled.get("usage"))
                except (GatewayError, TimeoutError):
                    pass
        if create_started and not confirmed:
            raise GatewayError(ErrorCode.TERMINATION) from None
        if error:
            if isinstance(error, (GatewayError, asyncio.CancelledError)):
                raise error
            if isinstance(error, TimeoutError):
                raise GatewayError(ErrorCode.TIMEOUT) from None
            raise GatewayError(ErrorCode.TRANSPORT) from None
        output = {"interaction_id": identifier, "frames_received": frames,
                  "action": "key_w:100ms", "termination": terminal, "development_only": True}
        return AdapterResult(output, {**output, "first_frame": first_frame,
                             "usage": usage.model_dump() if usage else None}, identifier, usage)
