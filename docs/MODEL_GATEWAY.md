# Model gateway (issue #3)

The backend now has adapters for Hydrogen and Carbon structured inference, Nano
Banana image generation, typed Decisions judgments, and a bounded Genie capability
probe. `GET /api/models/status` is read-only and never invokes a model. Existing
camera endpoints still start without model credentials or legacy Pharos services.

**All five services passed live capability checks on November 21, 2026.** Each
returned validated output and reported usage. Genie accepted the generated
reference, returned frames and confirmed completion. See the
[live verification record](MODEL_GATEWAY_VERIFICATION.md), including the initial
Nano Banana failure and the adapter correction. These capability probes do not
establish project throughput limits or submission eligibility.

## Configure and inspect

Install `backend/requirements.txt` into the project virtual environment. Copy
`.env.example` to `.env`, then set `GEMINI_API_KEY` (primary, with `GOOGLE_API_KEY`
accepted as a legacy alias) and optionally
`WORKSHOP_API_KEY`. Select one explicitly with `GEMINI_KEY_PROFILE=primary` or
`workshop`; there is no automatic project fallback. Environment variables take
precedence over `.env`. Neither key uses a
`VITE_` prefix or reaches the frontend. Credentials, raw upstream error bodies,
and connection authorization tokens are excluded from the gateway records.

Optional: copy `gateway.example.json` to `gateway.local.json` and set
`GATEWAY_CONFIG=gateway.local.json` in `.env`. Both local files are ignored by Git.
All API workers and CLI processes must use the same data directory and budget
configuration. The default directory is the repository's `.gateway/`.

From the repository root, on Windows:

```powershell
.\.venv\Scripts\python -m backend.gateway.smoke
.\.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

On macOS/Linux, use `.venv/bin/python` instead. The CLI and
`http://127.0.0.1:8000/api/models/status` show these states:

| State | Meaning |
| --- | --- |
| `configured` | Credentials exist, but calls are disabled in configuration. |
| `unverified` | Credentials are configured; there is no matching successful probe, or a request is pending. |
| `connected` | The latest dispatched call succeeded within the freshness interval. |
| `stale` | The latest success is older than the configured freshness interval. |
| `unavailable` | Credentials are absent, or the latest dispatched call failed. |

Verification is scoped to the credential fingerprint and configuration hash.
Status also exposes recorded latency, provider/local request IDs, whether usage
was reported, expired pricing, and budget blocks. A connected service can still
be blocked by a spending limit. Status does not list a guessed project quota.

Genie is optional and disabled by default. `GENIE_ENABLED=true` explicitly enables
billable world requests, while `false` blocks both the probe and session manager.
Default live smoke batches skip disabled Genie; explicit Genie requests fail without
provider traffic. Status includes `key_profile`, service `enabled`, and `optional`.

## Live capability checks

These commands make real, potentially billable requests once credentials exist:

```powershell
.\.venv\Scripts\python -m backend.gateway.smoke --live --services hydrogen carbon decisions
.\.venv\Scripts\python -m backend.gateway.smoke --live
```

The full run exercises image input and structured Hydrogen output; structured
Carbon compilation; Nano Banana image editing; Decisions image judgments; and
Genie creation, WebSocket connection, world setup, received JPEG pixels, a short
key press/release, and explicit completion. A generated Nano Banana reference is
passed to Genie when available; an individually selected Genie probe uses text.
All probe inputs are synthetic development fixtures, clearly excluded from
external-camera evaluation and submission evidence.

The report records every selected service's success/failure, exact model ID,
local/provider request IDs, payload/result hashes, reported usage and accounting
basis. The SQLite ledger records every attempt's latency and timestamps. A live
report exits nonzero if any selected service fails or lacks reported usage.
`request_id` is the locally generated durable attempt ID. The live stateless
inference and Decisions responses omitted provider IDs; their absence stays
explicit and is not replaced with a fabricated server ID. Genie must return its
provider interaction ID because termination depends on it.
One successful probe establishes access to that method, not the project's maximum
RPM/TPM or sustainable control cadence. Rejected quotas are recorded as
`quota_exceeded`; actual load limits remain to be measured with the integrated
workloads. There is no automatic throughput stress test.

## Backend integration contract

`backend/gateway` is a server-only library. Backend stages use `app.state.models`:
the observation route resolves retained source evidence before invoking Hydrogen.
Local capture/observation routes are intended for a loopback development server;
add deployment authentication before exposing these write routes publicly.

```python
from pydantic import BaseModel, Field
from backend.gateway import CallContext, Service

class Observation(BaseModel):
    visible_count: int = Field(ge=0)

result = await gateway.generate(
    Service.HYDROGEN,
    stage="observation",
    input_value=[
        {"type": "text", "text": "Count visible vehicles; do not infer hidden actors."},
        {"type": "image", "mime_type": "image/jpeg", "data": captured_base64},
    ],
    context=CallContext(
        episode_id="episode-1", source_id="camera-1", capture_id="capture-1",
        prompt_id="observation-v1", purpose="submission",
    ),
    response_model=Observation,
)
```

Use `generate` for Hydrogen, Carbon and Nano Banana, `judge` for Decisions, and
`probe_world` only for development smoke tests. `judge` validates yes/no (`noul`),
choice and score outputs, confidence, probability maps, score legends and rubric
consistency. Domain-specific acceptance thresholds belong to issues #8 and #11.

| Stage | Required service |
| --- | --- |
| `observation`, `navigation` | Hydrogen |
| `compilation`, `repair` | Carbon |
| `reference` | Nano Banana |
| `image_gate`, `temporal_gate` | Decisions |
| `world` | Genie |

Inputs are strings or inline text/image/video parts. URLs and mutable remote file
references are rejected. Serialize the complete payload once before the first
await; every retry sends those same bytes. Upstream capture must already be
bounded and immutable. Capture timing, allowed prefix validation, domain schemas,
and the 120-second episode cap remain owned by issues #2/#4/#5.

`require_submission_models(episode_id=..., source_id=..., capture_id=...)` must be
called by the eventual submission pipeline before declaring an episode complete.
It requires successful schema-validated observation and compilation traces for
that exact capture. Smoke/development traces, plain-text pings, navigation-only
calls, and the other three services cannot satisfy that pair. The domain schemas
and substantive output checks still belong to #5/#6; this gateway cannot judge
whether an arbitrary caller-supplied schema describes a meaningful street edit.

## Existing capture-branch configuration

The merged application uses one gateway and one operational ledger at `.gateway/`.
On startup, the former `data/gateway.sqlite3` ledger (or `MODEL_LEDGER_PATH`) is
read-only imported once per legacy request ID. Its full token and dollar
reservations remain charged, including failures and token overages; migration does
not manufacture fresh headroom or reinterpret reservations as provider invoices.
The original database and historical observation traces remain intact.

Existing `MODEL_*` environment ceilings override the equivalent JSON settings.
`MODEL_STAGE_BUDGET_USD` and `MODEL_STAGE_TOKEN_BUDGET` additionally cap each model
service across its stages, preventing a move from observation to navigation from
resetting the Hydrogen allowance. A legacy total request-token ceiling is split
between bounded input and output allowances. Explicit larger JSON allowances must
be reconciled deliberately. Copying `.env.example` therefore has stricter limits
than constructing `GatewayConfig()` with library defaults below.

`ObservationService` uses `generate(..., response_model=ExtractionOutput)` and
`gateway.trace(...)`; provider-facing schema bounds are projected to fit the
serving grammar while Pydantic still validates all bounds and evidence references.
Use `qualify=False` when a stage needs domain checks after structured output. Only
after those checks and durable artifact storage, call `accept_artifact(result,
artifact_id=..., artifact_sha256=...)` to bind the accepted output and qualify the
request. Observations follow this path so nonexistent frame references never count
as accepted submission evidence. No legacy `core.py` execution path or second
spending ledger remains active.

## Accounting and evidence

Defaults are a $20 total local budget with $5 held for explicitly tagged demo
calls, $5 and one million tokens per stage, two concurrent calls, two total
attempts, and a 45-second request deadline. Input and output allowances default
to 32,768 and 4,096 tokens per call. These are configurable development limits,
not measured provider limits. Only demo calls can consume the reserve; they
still obey stage and total budgets.

Before each attempt, a SQLite transaction reserves its token/dollar allowance.
Concurrent API/CLI processes cannot spend the same remaining allowance. Each
retry gets its own reservation and trace. Only explicit 429/502/503/504 responses
are retried with bounded delay; timeouts, malformed outputs, authentication
failures and Genie creation are not retried. Cleanup never needs a new spending
reservation.

Reported input, output and thought tokens reconcile successful and invalid-output
calls. Cost estimates use the configured standard rate card, the highest output
modality rate, and no cache discounts. They are conservative estimates, not
invoices. Missing usage, errors and interrupted calls retain their reservation.
An observed allowance overrun latches a durable block on new calls. Review the
evidence and reconcile the provider account before manually resolving a block;
there is deliberately no automatic budget reset.

**Local admission budgets are not provider-enforced billing caps.** Media input
token allowances must be sized for the actual workload; the gateway does not
perform token-count preflight. Output limits are sent to standard generation;
input allowances and Genie duration are enforced locally. Lost connections or
unreported usage can make the final upstream charge uncertain. Records retain
that uncertainty rather than reporting zero cost.

Rates were checked against the [official pricing page](https://aistudio.google.com/docs/pricing)
on November 21, 2026. The default rate card expires November 30 because Carbon's
published prices change December 1. Calls stop after expiry until a reviewed
rate card and validity date are configured.

`.gateway/usage.sqlite3` stores attempt IDs, requested model, stage, context,
credential/config fingerprints, hashes, UTC Unix timestamps, latency, outcomes,
and usage. `.gateway/artifacts/<sha256>.json` stores immutable request/result
evidence and smoke reports. Files publish atomically. They remain available when
a budget is exhausted or a process restarts; the directory is not publicly
served or committed. Keep this directory with the experiment evidence.

## Genie termination and recovery

The separate [Genie session runtime](GENIE_RUNTIME.md) now provides authenticated
streaming and bounded controls on top of this ledger. `probe_world` remains the
small capability probe; use the runtime for playable-world lifecycle integration.

The probe allows 15 seconds from connection through frame receipt/completion,
plus a separate five-second cleanup deadline. It sends key-up after 100 ms
independently of frame arrival, requests completion, and requires a terminal
acknowledgment. Connection/setup/stream failures trigger HTTP cancellation.
If termination is unconfirmed, new worlds are blocked across restarts.

For a recorded interrupted interaction with a known provider ID:

```powershell
.\.venv\Scripts\python -m backend.gateway.smoke --cancel-world LOCAL_REQUEST_ID
```

This uses the recorded interaction and credential fingerprint, bypasses new-work
budgets, and preserves previous failure evidence. If creation timed out before
an interaction ID was returned, automatic cancellation is impossible; check the
provider project before reconciling that record. Do not delete the ledger to
clear a termination block. Production playable sessions, scoped browser tokens,
accepted-image gating and the long-running watchdog remain issue #9.

## Verification and remaining acceptance

Run `.venv/Scripts/python -m pytest backend -q` on Windows (or the equivalent
virtualenv path), `npm run build`, and `npm test`. Gateway tests use HTTP and
session doubles; they are not live-service evidence. They exercise retries,
timeouts, cancellation, concurrency, restart persistence, budgets, malformed
responses, protocol separation, secret redaction, and required model roles.

Remaining integrated acceptance includes measured project behavior under actual
demo load and meaningful Hydrogen and Carbon episode outputs from real camera
evidence. Successful capability probes do not establish submission eligibility.

Protocol references: [Interactions](https://aistudio.google.com/api-reference/interactions-api/create-interaction),
[structured output](https://aistudio.google.com/docs/structured-output),
[image generation](https://aistudio.google.com/docs/image-generation),
[Decisions](https://aistudio.google.com/docs/decisions-api),
[Genie](https://aistudio.google.com/docs/genie). Genie uses the pinned
`google-genai==3.6.1` SDK for WebSocket transport; the HTTP inference and cleanup
adapters use `httpx` without SDK retries or automatic continuation.
