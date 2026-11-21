from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Service(StrEnum):
    HYDROGEN = "hydrogen"
    CARBON = "carbon"
    NANO_BANANA = "nano_banana"
    DECISIONS = "decisions"
    GENIE = "genie"


MODELS = {
    Service.HYDROGEN: "gemini-4-hydrogen",
    Service.CARBON: "gemini-4-carbon",
    Service.NANO_BANANA: "gemini-nano-banana-2.1",
    Service.DECISIONS: "gemini-decision-1",
    Service.GENIE: "genie-4",
}
STAGES = {
    "observation": Service.HYDROGEN,
    "navigation": Service.HYDROGEN,
    "compilation": Service.CARBON,
    "repair": Service.CARBON,
    "reference": Service.NANO_BANANA,
    "image_gate": Service.DECISIONS,
    "temporal_gate": Service.DECISIONS,
    "adversary": Service.CARBON,  # #24: attack generation, budgeted apart from repair
    "attack_judge": Service.DECISIONS,  # #24: offline per-attack scoring
    "world": Service.GENIE,
}
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
PROVIDER_STATUSES = frozenset({"INVALID_ARGUMENT", "NOT_FOUND", "PERMISSION_DENIED", "UNAUTHENTICATED",
    "RESOURCE_EXHAUSTED", "FAILED_PRECONDITION", "INTERNAL", "UNAVAILABLE", "DEADLINE_EXCEEDED",
    "OUT_OF_RANGE", "UNIMPLEMENTED", "CANCELLED", "UNKNOWN", "invalid_request"})


class CallContext(BaseModel):
    """Gateway-local contract; domain observations/edits remain owned by issue #2."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    episode_id: Identifier
    prompt_id: Identifier
    source_id: Identifier | None = None
    capture_id: Identifier | None = None
    attempt_id: Identifier | None = None
    original_media_sha256: tuple[Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")], ...] = ()
    purpose: Literal["development", "smoke", "submission", "demo"] = "development"


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    thought_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(ge=0)

    @property
    def charged_tokens(self) -> int:
        # Some API versions report thoughts separately. Never undercount them.
        return max(self.total_tokens, self.input_tokens + self.output_tokens + self.thought_tokens)


class ErrorCode(StrEnum):
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"
    INVALID_INPUT = "invalid_input"
    AUTH = "authentication_failed"
    QUOTA = "quota_exceeded"
    TIMEOUT = "timeout"
    TRANSPORT = "transport_error"
    UPSTREAM = "upstream_error"
    INVALID_OUTPUT = "invalid_output"
    INCOMPLETE_OUTPUT = "incomplete_output"
    BUDGET = "budget_exceeded"
    CONCURRENCY = "concurrency_limit"
    TERMINATION = "termination_unconfirmed"
    STORAGE = "storage_error"
    CANCELLED = "cancelled"


MESSAGES = {
    ErrorCode.NOT_CONFIGURED: "Set the backend key selected by GEMINI_KEY_PROFILE: GEMINI_API_KEY or WORKSHOP_API_KEY.",
    ErrorCode.DISABLED: "Model calls are disabled in the gateway configuration.",
    ErrorCode.INVALID_INPUT: "The request does not satisfy the gateway contract.",
    ErrorCode.AUTH: "The project credential cannot access this service.",
    ErrorCode.QUOTA: "The provider rejected this request because of quota or rate limits.",
    ErrorCode.TIMEOUT: "The request timed out; its reserved usage remains charged locally.",
    ErrorCode.TRANSPORT: "The provider connection failed; usage may be unknown.",
    ErrorCode.UPSTREAM: "The provider rejected or failed the request.",
    ErrorCode.INVALID_OUTPUT: "The provider response failed validation.",
    ErrorCode.INCOMPLETE_OUTPUT: "The provider exhausted its output allowance without completing the response.",
    ErrorCode.BUDGET: "A configured usage budget or request allowance was exceeded.",
    ErrorCode.CONCURRENCY: "The configured number of concurrent calls is already in flight.",
    ErrorCode.TERMINATION: "Genie termination is unconfirmed. Resolve the recorded interaction before creating another.",
    ErrorCode.STORAGE: "The gateway could not durably record this request or result.",
    ErrorCode.CANCELLED: "The local caller cancelled; unknown usage retains its reservation.",
}


class GatewayError(Exception):
    """Only fixed messages cross the API/log boundary, never upstream error bodies."""

    def __init__(self, code: ErrorCode, *, retryable: bool = False,
                 request_id: str | None = None, retry_after: float = 0,
                 http_status: int | None = None, provider_status: str | None = None, status: int | None = None):
        self.code = code
        self.retryable = retryable
        self.request_id = request_id
        self.retry_after = retry_after
        http_status = http_status if http_status is not None else status
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.status = self.http_status  # World cleanup inspects explicit rejected creates.
        self.provider_status = provider_status if isinstance(provider_status, str) and provider_status in PROVIDER_STATUSES else None
        super().__init__(MESSAGES[code])

    def public(self) -> dict:
        return {"code": self.code.value, "message": str(self),
                "retryable": self.retryable, "request_id": self.request_id,
                "http_status": self.http_status, "provider_status": self.provider_status}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class PreparedCall:
    service: Service
    stage: str
    body: bytes
    context: CallContext
    config_hash: str
    input_token_bound: int
    output_token_bound: int
    response_model: type[BaseModel] | None = None
    qualify: bool = True

    @property
    def input_hash(self) -> str:
        return digest(self.body)


@dataclass
class AdapterResult:
    output: Any
    evidence: dict
    provider_request_id: str | None
    usage: Usage | None


@dataclass
class GenieCallState:
    """Per-call progress retained even when a probe raises or is cancelled."""

    termination_pending: bool = False
    termination: str | None = None
    usage: Usage | None = None


@dataclass(frozen=True)
class CallResult:
    request_id: str
    provider_request_id: str | None
    model_id: str
    input_hash: str
    result_hash: str
    output: Any
    usage: Usage | None
    accounting: Literal["reported_usage_upper_bound", "reservation_retained"]
