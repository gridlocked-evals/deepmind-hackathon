from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..gateway.contracts import CallContext, Identifier

Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Key = Literal["key_w", "key_s", "key_a", "key_d", "arrow_up", "arrow_down", "arrow_left", "arrow_right"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class AcceptedReference(Contract):
    """Minimal trusted handoff from #8; not a replacement for its domain models."""
    schema_version: Literal[1] = 1
    episode_id: Identifier
    source_id: Identifier
    capture_id: Identifier
    reference_id: Identifier
    reference_sha256: Hash
    mime_type: Literal["image/jpeg", "image/png"]
    gate_decision_id: Identifier
    gate_artifact_sha256: Hash
    decision: Literal["accept"]


class WorldRequest(Contract):
    accepted_reference: Hash
    context: CallContext
    prompt: str = Field(min_length=1, max_length=4000)
    seconds: float = Field(default=15, ge=1, le=115, allow_inf_nan=False)
    max_usd: Decimal = Field(default=Decimal("2.5"), gt=0, le=20, allow_inf_nan=False)


class WorldLimits(Contract):
    max_seconds: float = Field(default=30, ge=1, le=115)
    max_usd: Decimal = Field(default=Decimal("3"), gt=0)
    attach_seconds: float = Field(default=10, gt=0)
    idle_seconds: float = Field(default=3, gt=0)
    frame_timeout: float = Field(default=12, gt=0)
    send_seconds: float = Field(default=.5, gt=0)
    max_key_ms: int = Field(default=500, ge=1, le=1000)
    max_recording_bytes: int = Field(default=128 * 1024 * 1024, ge=1024)
    max_sessions: int = Field(default=16, ge=1)
    max_steering_bytes: int = Field(default=8000, ge=0)


class KeyCommand(Contract):
    kind: Literal["key"]
    owner: Literal["human", "defender"]
    key: Key
    hold_ms: int = Field(ge=1, le=1000)


class Takeover(Contract):
    kind: Literal["takeover"]
    owner: Literal["human", "defender"]


class Steering(Contract):
    kind: Literal["steer"]
    owner: Literal["adversary"]
    text: str = Field(min_length=1, max_length=1000)


class Stop(Contract):
    kind: Literal["stop", "emergency_stop", "release", "heartbeat"]


Command = Annotated[KeyCommand | Takeover | Steering | Stop, Field(discriminator="kind")]
