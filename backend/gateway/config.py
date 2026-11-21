from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_CEILING
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from dotenv import load_dotenv

from .contracts import STAGES, Service, Usage, canonical, digest

ROOT = Path(__file__).resolve().parents[2]


class StageBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tokens: int = Field(default=1_000_000, ge=1)
    usd: Decimal = Field(default=Decimal("5"), gt=0, allow_inf_nan=False)


class Rate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    output_per_million: Decimal = Field(ge=0, allow_inf_nan=False)

    def charge(self, input_tokens: int, output_tokens: int) -> int:
        # USD/million tokens is also micro-USD/token. Integer storage avoids drift.
        return int((input_tokens * self.input_per_million +
                    output_tokens * self.output_per_million).to_integral_value(rounding=ROUND_CEILING))

    def usage_charge(self, usage: Usage) -> int:
        return self.charge(usage.input_tokens, usage.charged_tokens - usage.input_tokens)


def dollars(value: Decimal) -> int:
    return int((value * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


def default_rates() -> dict[Service, Rate]:
    # Standard published rates checked 2026-11-21. Use the highest output modality
    # rate and ignore discounts; estimates are upper bounds, not provider invoices.
    return {
        Service.HYDROGEN: Rate(input_per_million="1.5", output_per_million="7.5"),
        Service.CARBON: Rate(input_per_million="2", output_per_million="10"),
        Service.NANO_BANANA: Rate(input_per_million="1.5", output_per_million="30"),
        Service.DECISIONS: Rate(input_per_million="0.1", output_per_million="0"),
        Service.GENIE: Rate(input_per_million="1.5", output_per_million="17.5"),
    }


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    genie_enabled: bool = False
    key_profile: Literal["primary", "workshop"] = "primary"
    data_dir: Path = ROOT / ".gateway"
    legacy_ledger_path: Path | None = None
    total_usd: Decimal = Field(default=Decimal("20"), gt=0, allow_inf_nan=False)
    demo_reserve_usd: Decimal = Field(default=Decimal("5"), ge=0, allow_inf_nan=False)
    max_concurrency: int = Field(default=2, ge=1, le=16)
    max_attempts: int = Field(default=2, ge=1, le=3)
    timeout_seconds: float = Field(default=45, gt=0, le=120, allow_inf_nan=False)
    retry_delay_seconds: float = Field(default=2, ge=0, le=10, allow_inf_nan=False)
    stale_after_seconds: int = Field(default=900, ge=1)
    input_tokens_per_call: int = Field(default=32768, ge=1, le=1_000_000)
    output_tokens_per_call: int = Field(default=4096, ge=1, le=65536)
    image_output_tokens_per_call: int = Field(default=8192, ge=1, le=65536)
    max_request_tokens: int = Field(default=1_000_000, ge=1)
    max_request_usd: Decimal = Field(default=Decimal("5"), gt=0, allow_inf_nan=False)
    max_request_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    max_response_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    genie_seconds: float = Field(default=15, gt=0, le=30, allow_inf_nan=False)
    cleanup_seconds: float = Field(default=5, gt=0, le=15, allow_inf_nan=False)
    rates_valid_through: date = date(2026, 11, 30)
    rates: dict[Service, Rate] = Field(default_factory=default_rates)
    stages: dict[str, StageBudget] = Field(default_factory=lambda: {s: StageBudget() for s in STAGES})
    service_budgets: dict[Service, StageBudget] | None = None

    @model_validator(mode="after")
    def check_configuration(self):
        if self.demo_reserve_usd >= self.total_usd:
            raise ValueError("Demo reserve must be smaller than the total budget")
        if set(self.rates) != set(Service) or set(self.stages) != set(STAGES):
            raise ValueError("All service rates and stage budgets must be configured")
        if self.service_budgets is not None and set(self.service_budgets) != set(Service):
            raise ValueError("All aggregate service budgets must be configured")
        return self

    @property
    def config_hash(self) -> str:
        return digest(canonical(self.model_dump(mode="json", exclude={"data_dir", "legacy_ledger_path"})))

    @classmethod
    def from_environment(cls) -> GatewayConfig:
        load_dotenv(ROOT / ".env", override=False)
        path = os.getenv("GATEWAY_CONFIG")
        config_path = Path(path) if path else None
        if config_path and not config_path.is_absolute():
            config_path = ROOT / config_path
        values = json.loads(config_path.read_text("utf-8")) if config_path else {}
        explicit_token_bounds = {name for name in ("input_tokens_per_call", "output_tokens_per_call") if name in values}
        # Existing environment caps remain authoritative during the migration.
        # They govern this one ledger; no second operational gateway is created.
        mapping = {"MODEL_TOTAL_BUDGET_USD": "total_usd", "MODEL_DEMO_RESERVE_USD": "demo_reserve_usd",
                   "MODEL_MAX_CONCURRENCY": "max_concurrency", "MODEL_TIMEOUT_SECONDS": "timeout_seconds",
                   "MODEL_STALE_SECONDS": "stale_after_seconds", "MODEL_MAX_REQUEST_TOKENS": "max_request_tokens",
                   "MODEL_MAX_REQUEST_USD": "max_request_usd"}
        for name, field in mapping.items():
            if name in os.environ:
                values[field] = os.environ[name]
        if "MODEL_MAX_REQUEST_TOKENS" in os.environ:
            maximum = int(os.environ["MODEL_MAX_REQUEST_TOKENS"])
            output = int(values.get("output_tokens_per_call", cls.model_fields["output_tokens_per_call"].default))
            if not explicit_token_bounds:
                values["input_tokens_per_call"] = maximum - output
            elif int(values.get("input_tokens_per_call", cls.model_fields["input_tokens_per_call"].default)) + output > maximum:
                raise ValueError("Explicit input/output allowances exceed MODEL_MAX_REQUEST_TOKENS")
        if "MODEL_MAX_RETRIES" in os.environ:
            values["max_attempts"] = int(os.environ["MODEL_MAX_RETRIES"]) + 1
        if "MODEL_STAGE_TOKEN_BUDGET" in os.environ or "MODEL_STAGE_BUDGET_USD" in os.environ:
            budget = StageBudget(tokens=os.getenv("MODEL_STAGE_TOKEN_BUDGET", "100000"),
                                 usd=os.getenv("MODEL_STAGE_BUDGET_USD", "5")).model_dump(mode="json")
            values["stages"] = {stage: dict(budget) for stage in STAGES}
            values["service_budgets"] = {service: dict(budget) for service in Service}
        if "GENIE_ENABLED" in os.environ:
            flag = os.environ["GENIE_ENABLED"].strip().lower()
            if flag not in {"true", "false"}:
                raise ValueError("GENIE_ENABLED must be true or false")
            values["genie_enabled"] = flag == "true"
        if "GEMINI_KEY_PROFILE" in os.environ:
            values["key_profile"] = os.environ["GEMINI_KEY_PROFILE"]
        values.setdefault("legacy_ledger_path", os.getenv("MODEL_LEDGER_PATH", "data/gateway.sqlite3"))
        for field in ("data_dir", "legacy_ledger_path"):
            if values.get(field) is not None:
                value = Path(values[field])
                values[field] = value if value.is_absolute() else ROOT / value
        return cls.model_validate(values)


def backend_key(profile: str = "primary") -> str | None:
    # A missing selected credential must never silently switch projects.
    return os.getenv("WORKSHOP_API_KEY" if profile == "workshop" else "GEMINI_API_KEY") or None
