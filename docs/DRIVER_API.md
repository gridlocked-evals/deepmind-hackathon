# Gridlocked simulator driver

`POST /api/driver/decision` asks the existing **Gemini Decisions** (`gemini-decision-1`) gateway for one
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
| `step` | Contiguous integers 0–59, starting at 0 |
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
  "model_id": "gemini-decision-1",
  "request_id": "231c4db5-699b-4ea8-81e9-2fa7fa1dec6f",
  "decision": {
    "action": "accelerate",
    "target_speed_mps": 6.5,
    "reason": "Decisions selected accelerate; target 6.50 m/s.",
    "confidence": 0.9
  },
  "max_steps": 60
}
```

`action` is `accelerate`, `cruise`, `brake`, or `stop`. Target speed is finite,
between 0 and 11.176 m/s (25 mph), and no higher than the submitted speed limit.
`stop` requires a zero target; `brake` cannot increase the submitted speed.
`confidence` is finite and between 0 and 1. `reason` contains 1–240 characters.
One typed [Decisions `choice` question](https://aistudio.google.com/docs/decisions-api) selects one of four targets: stop at 0,
brake at current speed minus 2.5 m/s, cruise at current speed, or accelerate at
current speed plus 2.5 m/s. Candidate speeds are bounded to 0 through
`min(speed_limit_mps, 11.176)` before being sent to the provider. `reason` is a
fixed summary of that selection, not generated reasoning. Invalid provider output
returns an error, with no fallback decision.
`request_id` identifies the existing gateway's recorded call; confidence is the
model's estimate, not a calibrated assurance of safe driving.

The browser must verify the run and step still match the active session before
using the result. A stopped/restarted session or replaced route must discard late
responses. It must stop after an error, 60 decisions, or 60 simulated seconds. A target is a
simulator control input, not permission to bypass local collision/signal guards.

## Continuous control

The first decision starts motion. Thereafter a single animation loop keeps applying
the latest accepted target while the next request is in flight. Requests begin at
most once per second and never overlap within a run. A newly accepted target
replaces the previous target immediately; motion and the local physics guards
continue across that handoff.

The browser measures command age from the observation's wall-clock capture time,
including inference latency. At three seconds without a fresh command it stops
and reports an error; late responses cannot restart the run. The initial request
also has a three-second deadline. There are no scheduled three-second pauses.

## Limits, retries, and errors

Each process retains at most 64 runs for 30 minutes after their most recent
accepted decision completes. A run accepts at most 60 decisions and allows only
one in-flight call. Across the driver endpoint, at most two calls are in flight.
The gateway's own concurrency, configured retries, request allowances, stage
budgets, durable reservations, and total spending limits still apply unchanged.
A decision may therefore represent more than one provider attempt under the
gateway's existing retry policy. The driver bounds each admitted gateway operation
to five seconds, so a browser abort cannot occupy an admission slot indefinitely.

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

The dedicated `simulator_driver` stage inherits the navigation budget when loading
a legacy complete stage configuration. The visual Genie navigation stage remains
on Hydrogen. Global spending and protected demo reserves still apply.

## Trace export

After a run ends, the driver panel shows **Download trace (JSON)**. This applies
to a completed, decision-limited, stopped, failed or interrupted run. The file
uses schema `gridlocked-driver-trace/2` (`src/agent/trace.ts`) and is labelled
`provenance.source: "synthetic_simulator_state"`. It is structured simulator
state. It is not camera or pixel input, a Hydrogen/Genie visual trace, or a
Decisions safety score. Each step holds the following:

- the exact observation sent to this endpoint, whose `simulation_time` is the
  request snapshot; inference can finish after simulation has advanced;
- the request ID and the model's **proposed** decision;
- the **applied** motion: start and end simulation time, start and end speed,
  and distance travelled;
- `guard`: whether the local leader or red-line guard changed the applied
  motion. The reason is `leader_guard`, `red_line_guard` or `none`, and each
  event records the simulation time it started.

`terminal.state` is `route_complete`, `decision_limit`, `time_limit`, `stopped_by_user`,
`error` or `interrupted` (the tab was hidden). An `error` carries only the
server's redacted message or the panel's own fixed text; any other failure,
such as a non-JSON response, is recorded as "The driving request failed." The
panel displays that same sanitized error. A
request still pending when the run ends adds no step, and a hold cut short ends
at the stop time. A hold stopped before its first physics tick still appears,
with zero applied motion, because the model's decision was received. Motion while
waiting for a replacement belongs to the previous decision's applied interval.
The bounds record `max_decisions` (60), `decision_interval_seconds` (1),
`max_command_age_seconds` (3), and `max_simulation_seconds` (60). Version 2 replaces
the old fixed `seconds_per_decision` field; the current reader rejects earlier
schema versions rather than interpreting their pause-and-sprint timing as continuous.
The export carries no credentials or provider data beyond the model ID, request ID
and decision.

The compact download refers to the latest run and clears when returning to
replay or changing scene. Scenario Lab also retains the last four completed or
interrupted runs in this tab, including this action trace under `driverTrace`
in its extended export. Download before reloading the page. The extended
[scenario export](SCENARIO_INJECTION.md#trace-comparison-and-replay) includes
event state and recorded frames for playback; the compact format alone does not.

## Local setup and provenance

Configure `GEMINI_API_KEY` or `GOOGLE_API_KEY` on the backend only, as documented
by the existing model gateway. Never put the credential in a `VITE_*` variable or
browser request. `/api/models/status` reports configuration; it cannot guarantee
model entitlement, remaining quota, or a successful future call.

The endpoint uses `Service.DECISIONS`, `stage="simulator_driver"`, the existing typed
choice support, and `CallContext(purpose="development")`. Its episode ID
is `sim-driver:<run_id>` and prompt ID is `sim-driver-decisions-v2:step-<step>`. It does not
forge source/capture IDs or consume the protected demo reserve as camera evidence.
The Decisions choice response contains no generated text or thinking tokens.
Historical Hydrogen scenario checks and their low-thinking policy are recorded
in [scenario verification](SCENARIO_VERIFICATION.md); they do not describe the
current Decisions driver.
Keep the development server on loopback; this endpoint follows the local pipeline
API's unauthenticated setup and is not a public multi-user service.

Run the offline tests with:

```sh
.venv/bin/python -m pytest backend/test_driver.py -q
```

Tests use only fake gateways and do not call a paid provider.
