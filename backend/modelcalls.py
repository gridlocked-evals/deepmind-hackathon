"""Turn gateway results into ``element119.v1`` ModelCall provenance."""
from __future__ import annotations

from datetime import datetime
import json
from uuid import uuid4

from .contracts import ModelCall
from .gateway import GatewayError, ModelGateway
from .gateway.contracts import MODELS, CallResult, ErrorCode, Service

_OUTCOMES = {
    ErrorCode.TIMEOUT: "timeout", ErrorCode.QUOTA: "quota", ErrorCode.INVALID_OUTPUT: "invalid_output",
    ErrorCode.BUDGET: "blocked", ErrorCode.DISABLED: "blocked", ErrorCode.NOT_CONFIGURED: "blocked",
    ErrorCode.CONCURRENCY: "blocked", ErrorCode.INVALID_INPUT: "blocked",
}


def _ids(domain_hashes: list[str], gateway_hash: str | None) -> list[str]:
    hashes = list(dict.fromkeys(domain_hashes))
    if gateway_hash:
        hashes.append("sha256:" + gateway_hash)
    return hashes


def from_result(gateway: ModelGateway, service: Service, result: CallResult, *, prompt_id: str,
                domain_hashes: list[str], requested_at: datetime, completed_at: datetime) -> ModelCall:
    usage = result.usage
    cost = None
    if usage:
        cost = gateway.config.rates[service].usage_charge(usage) / 1_000_000
    return ModelCall(
        id=f"call_{uuid4().hex}", service=service.value, modelId=result.model_id, promptId=prompt_id,
        configId=gateway.config.config_hash[:16],
        # Provider IDs are often omitted; fall back to the gateway ledger ID, labelled as such.
        requestId=result.provider_request_id or f"gateway:{result.request_id}",
        inputHashes=_ids(domain_hashes, result.input_hash), requestedAt=requested_at, completedAt=completed_at,
        latencyMs=(completed_at - requested_at).total_seconds() * 1000,
        inputTokens=usage.input_tokens if usage else None,
        outputTokens=(usage.charged_tokens - usage.input_tokens) if usage else None,
        costUsd=cost, outcome="ok")


def from_error(gateway: ModelGateway, service: Service, error: GatewayError, *, prompt_id: str,
               domain_hashes: list[str], requested_at: datetime, completed_at: datetime) -> ModelCall:
    """A failed attempt still gets a ModelCall so attempt history stays complete."""
    row = gateway.ledger.get(error.request_id) if error.request_id else None
    usage = json.loads(row["usage"]) if row and row.get("usage") else None
    return ModelCall(
        id=f"call_{uuid4().hex}", service=service.value, modelId=MODELS[service], promptId=prompt_id,
        configId=gateway.config.config_hash[:16],
        requestId=f"gateway:{error.request_id}" if error.request_id else None,
        inputHashes=_ids(domain_hashes, row["input_hash"] if row else None), requestedAt=requested_at, completedAt=completed_at,
        latencyMs=(completed_at - requested_at).total_seconds() * 1000,
        inputTokens=usage["input_tokens"] if usage else None,
        outputTokens=max(usage["output_tokens"]+usage.get("thought_tokens",0), usage["total_tokens"]-usage["input_tokens"]) if usage else None,
        costUsd=row["charged_microusd"]/1_000_000 if usage else None,
        outcome=_OUTCOMES.get(error.code, "error"))
