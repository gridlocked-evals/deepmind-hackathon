# Gridlocked simulator driver

`POST /api/driver/decision` asks the existing **Gemini Hydrogen** gateway for one
longitudinal action for the selected simulated car. The browser supplies structured
simulator state and applies a validated target speed through the local traffic
physics. Traffic signals, headway, stop lines, route geometry, and acceleration
limits remain simulator constraints.

This is synthetic development input. It does **not** process camera images, claim
to observe real cars, or implement [#10's visual Genie navigation loop](https://github.com/gridlocked-evals/deepmind-hackathon/issues/10).
The image-processing and captured-evidence pipeline remains a separate integration.
No model decision is substituted when the provider is unavailable; the browser
must pause model driving and show the error. Local demo traffic can run separately.

## Request

Send `Content-Type: application/json`, with at most 8 KiB of UTF-8 JSON. Unknown
fields, duplicate JSON keys, booleans in numeric fields, numeric strings, NaN,
Infinity, and out-of-range values are rejected before a gateway call.

```json
{
  "run_id": "5f469130-6c20-478c-8b05-c90b02530b52",
  "step": 0,
  "observation": {
    "scenario_id": "midtown",
    "simulation_time": 42,
    "speed_mps": 4,
    "speed_limit_mps": 11.176,
    "remaining_m": 600,
    "signal": "green",
    "stop_distance_m": 80,
    "lead_gap_m": null,
    "lead_speed_mps": null,
    "upcoming_turn_degrees": 0,
    "route_label": "Broadway to 42nd Street"
  }
}
```

| Field | Contract |
| --- | --- |
| `run_id` | 8–64 ASCII letters, digits, `_`, or `-`; use a fresh UUID per session/reset/route change |
| `step` | Contiguous integers 0–11, starting at 0 |
| `scenario_id` | 1–80 letters, digits, `_`, `.`, `:`, or `-`; constant within a run |
| `simulation_time` | 0–86,400 simulated seconds |
| `speed_mps`, `lead_speed_mps` | 0–50 m/s; lead speed can be `null` |
| `speed_limit_mps` | Greater than 0 and at most 50 m/s |
| `remaining_m` | 0–100,000 metres on the selected route |
| `signal` | `green`, `amber`, `red`, or `unknown` |
| `stop_distance_m`, `lead_gap_m` | 0–10,000 metres, or `null` when unavailable |
| `upcoming_turn_degrees` | Signed angle from −180 to 180 degrees |
| `route_label` | 1–160 characters; treated as untrusted data, never a prompt instruction |

## Response

```json
{
  "run_id": "5f469130-6c20-478c-8b05-c90b02530b52",
  "step": 0,
  "model_id": "gemini-4-hydrogen",
  "request_id": "231c4db5-699b-4ea8-81e9-2fa7fa1dec6f",
  "decision": {
    "action": "accelerate",
    "target_speed_mps": 6,
    "reason": "The route is clear and the signal is green.",
    "confidence": 0.9
  },
  "max_steps": 12
}
```

`action` is `accelerate`, `cruise`, `brake`, or `stop`. Target speed is finite,
between 0 and 11.176 m/s (25 mph), and no higher than the submitted speed limit.
`stop` requires a zero target; `brake` cannot increase the submitted speed.
`confidence` is finite and between 0 and 1. `reason` contains 1–240 characters.
Invalid provider output returns an error, with no fallback decision or clamping.
`request_id` identifies the existing gateway's recorded call; confidence is the
model's estimate, not a calibrated assurance of safe driving.

The browser must verify the run and step still match the active session before
using the result. A stopped/restarted session or replaced route must discard late
responses. It must pause after an error and after 12 decisions. A target is a
simulator control input, not permission to bypass local collision/signal guards.

## Limits, retries, and errors

Each process retains at most 64 runs for 30 minutes after their most recent
accepted decision completes. A run accepts at most 12 decisions and allows only
one in-flight call. Across the driver endpoint, at most two calls are in flight.
The gateway's own concurrency, configured retries, request allowances, stage
budgets, durable reservations, and total spending limits still apply unchanged.
A decision may therefore represent more than one provider attempt under the
gateway's existing retry policy.

An identical `(run_id, step, observation)` retry returns the cached success or
cached failure without calling Gemini again. A changed payload for an existing
step, skipped step, or changed scenario returns 409. An in-flight duplicate
returns 429 without dispatching another call. An admitted failed/cancelled call
consumes its step because provider usage may already have occurred. Stop the UI
session on failure; retries of that same step are for recovering its recorded
result, not causing a new model request. Rejected admissions do not consume a step.

All errors use a fixed public gateway message:

```json
{
  "detail": {
    "code": "not_configured",
    "message": "Set GEMINI_API_KEY or GOOGLE_API_KEY on the backend.",
    "retryable": false,
    "request_id": null
  }
}
```

| HTTP status | Typical `detail.code` |
| --- | --- |
| 413 / 415 / 422 | `invalid_input` (size / content type / schema) |
| 409 | `invalid_input` (run or step conflict) |
| 429 | `concurrency_limit`, `quota_exceeded`, `budget_exceeded` |
| 503 | `not_configured`, `disabled`, `authentication_failed`, `cancelled` |
| 504 | `timeout` |
| 502 | `invalid_output`, `upstream_error`, `transport_error`, `storage_error` |

Provider error bodies and unexpected exception text are never returned. There is
no application-level automatic retry beyond the existing gateway behavior.
The registry is intentionally process-local: use a single local API worker for
testing. It is reset by a backend restart and is not a distributed idempotency
store. The durable gateway ledger continues to enforce spending across calls.

## Local setup and provenance

Configure `GEMINI_API_KEY` or `GOOGLE_API_KEY` on the backend only, as documented
by the existing model gateway. Never put the credential in a `VITE_*` variable or
browser request. `/api/models/status` reports configuration; it cannot guarantee
model entitlement, remaining quota, or a successful future call.

The endpoint uses `Service.HYDROGEN`, `stage="navigation"`, the existing structured
response schema support, and `CallContext(purpose="development")`. Its episode ID
is `sim-driver:<run_id>` and prompt ID is `sim-driver-v1:step-<step>`. It does not
forge source/capture IDs or consume the protected demo reserve as camera evidence.
Keep the development server on loopback; this endpoint follows the local pipeline
API's unauthenticated setup and is not a public multi-user service.

Run the offline tests with:

```sh
.venv/bin/python -m pytest backend/test_driver.py -q
```

Tests use only fake gateways and do not call a paid provider.
