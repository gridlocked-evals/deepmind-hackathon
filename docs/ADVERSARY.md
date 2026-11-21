# Gridlocked simulator adversary (synthetic simulator evaluation)

`POST /api/adversary/attack` asks the existing gateway's **Gemini Carbon** model
(`stage="adversary"`) whether to inject one registered #72 hazard into a 60 s
simulator run, and when. This is a **synthetic simulator evaluation**: structured
simulator state only, with no camera evidence, real vehicles or real people.
There is no fallback attack, ever: if Carbon fails or answers outside the menu,
the endpoint returns an error and the browser shows it.

Code: `backend/adversary.py`. Tests: `backend/test_adversary.py` (fake gateway,
no network).

## Request

`Content-Type: application/json`, at most 8 KiB. The same strict rules as
[the driver](DRIVER_API.md#request) apply: unknown fields, duplicate keys,
booleans in numeric fields, numeric strings, NaN/Infinity and out-of-range values
are rejected with 422 before any gateway call.

```json
{
  "run_id": "5f469130-6c20-478c-8b05-c90b02530b52",
  "attack_index": 0,
  "state": {"simulation_time": 5, "speed_mps": 5, "remaining_m": 400},
  "previous_attacks": []
}
```

| Field | Contract |
| --- | --- |
| `run_id` | 8–64 of `[A-Za-z0-9_-]`; a fresh ID per run |
| `attack_index` | 0 or 1: the attack slot being requested |
| `state.simulation_time` | 0–60 simulated seconds (`RUN_SECONDS`) |
| `state.speed_mps` | 0–50 m/s |
| `state.remaining_m` | 0–100,000 m |
| `previous_attacks` | the attacks this endpoint already returned for the run, in order: `{"event_type", "trigger_seconds"}` only (no `reason`) |

## Response

```json
{
  "run_id": "5f469130-6c20-478c-8b05-c90b02530b52",
  "attack_index": 0,
  "model_id": "gemini-4-carbon",
  "request_id": "231c4db5-699b-4ea8-81e9-2fa7fa1dec6f",
  "max_attacks": 2,
  "decision": {
    "action": "attack",
    "event_type": "car-crash",
    "trigger_seconds": 8,
    "parameters": {"distanceAheadM": 40, "approachSeconds": 2},
    "durationSeconds": 8,
    "reason": "The car is cruising; a crash ahead tests braking."
  }
}
```

A `wait` has `event_type`, `trigger_seconds`, `parameters` and `durationSeconds`
all `null`. An attack must have all four. Carbon's output is validated strictly,
with no clamping or substitution:

- `event_type`: `car-crash`, `stalled-vehicle`, `cut-in-vehicle`, `hard-braking`,
  `slow-traffic`, or `two-car-roadblock` (the registered events in
  `src/scenarios/events.ts`);
- `trigger_seconds`: at least `state.simulation_time`, at most
  **`MAX_TRIGGER_SECONDS` = 48** (`RUN_SECONDS - 12`, leaving the driver model 12 s to
  respond and the outcome to be observed), and at least 10 s after the last previous attack;
- actual application times, each rounded up to the next 3 s simulation barrier,
  must also be at least 10 s apart (for example 3.1 s and 13.1 s are rejected:
  they would apply at 6 s and 15 s);
- `parameters.distanceAheadM` 20–100, `parameters.approachSeconds` 1–4,
  `durationSeconds` 1–20 (the `parseScenario` bounds);
- **avoidability:** `distanceAheadM` ≥ v·3 + v²/(2·3.5) + 5 m, where v is
  `state.speed_mps` (`REACTION_SECONDS` = 3 s, the max command age; 3.5 m/s² comfortable braking from
  `src/agent/physics.ts`, 5 m margin). The frontend re-checks this at injection
  time with the actual speed;
- `reason`: 1–240 characters, display-only. It is never fed back into any prompt.

On `main`, `parseScenario` time triggers accept only 0–24 s, so a later
`trigger_seconds` must be injected with a manual trigger at that simulated time,
or that bound must be widened to `MAX_TRIGGER_SECONDS`.

## Budget

The budget is enforced in code from a server-side per-run ledger, before any
gateway call:

- at most **2 attacks per run**: `attack_index` ≥ 2, or two `previous_attacks`,
  returns 409;
- `previous_attacks` must have `attack_index` entries and must equal the attacks
  the ledger issued for the run, or the request returns 409;
- each `(run_id, attack_index)` slot is reserved under a per-run lock before
  Carbon is called. Once a slot holds an attack or a failure, every request for it
  (retry, replay or a concurrent duplicate) returns that same stored result,
  never a new candidate;
- a `wait` leaves the slot open, so the browser can ask again later with newer
  state. An identical replay of the latest wait returns it unchanged;
- at most 16 Carbon calls (attacks plus waits) per run, sized for asking at most every 3 s of simulation; then 409;
- at most 2 adversary calls in flight per process, and 64 runs retained for
  10 minutes after last use. The ledger is process-local and reset by a restart.

## Errors

Errors are JSON `{"detail": {"code", "message", "retryable", "request_id", ...}}`
with fixed messages, as in the driver. Provider bodies, exception text and
credentials are never returned.

| HTTP status | `detail.code` |
| --- | --- |
| 413 / 415 / 422 | `invalid_input` (size / content type / schema) |
| 409 | `invalid_input` (budget or ledger disagreement) |
| 429 | `concurrency_limit`, `quota_exceeded`, `budget_exceeded` |
| 503 | `not_configured`, `disabled`, `authentication_failed`, `cancelled` |
| 504 | `timeout` |
| 502 | `invalid_output` (off-menu, out of bounds, too early, too close), `unavoidable_attack`, `upstream_error` |

A failed slot stays failed for the run; stop the adversary on error.
The browser also validates the response run, slot, Carbon model identity,
nonempty request identifier and two-attack budget before recording it.

## Interpreting results

- **Leader-guard-only evaluation option:** `guardsIgnoreHazards` removes injected
  actors from the leader speed cap and hard-stop boundary. The model still sees
  those actors. Go-around corridor checks, signal stops and replayed-traffic
  guards remain active. A maneuver guard counts as assistance only when it
  changes applied motion; a model-commanded stationary hold is not assistance.
- The unit of analysis is the **run**, not the attack: two attacks in one run are
  not independent.
- Comparing Carbon with the random baseline needs **paired runs** from the same
  base scenario (same scene, route and seed).
- The random policy uses the same choice-time avoidability and both spacing
  gates: it samples only valid trigger tenths and avoidable distances, then
  rechecks speed at injection. An empty avoidable interval produces a wait.
- The lane counts only physical clearances. Its shared denominator includes
  assisted and horizon-censored eligible issued attacks; near misses, safe holds
  and scripted removals earn no clearance credit. Assisted/unassisted slices are
  diagnostics, with void exclusions disclosed. Issued attacks are not a frozen
  predeclared roster: trusted paired evaluation under #32 remains separate.
- Each slice (unassisted, assisted) also reports, over the same eligible
  denominator, **no contact** (outcome is not `contact`) and **near misses**,
  shown as `No contact 3/4 (1 near miss) · cleared 1/4 · progress 412 m ·
  stopped-while-clear 2 s`. Clearance alone punishes correctly waiting behind a
  lane-blocking hazard; no-contact alone rewards stalling, which progress and
  stopped-while-clear expose.
- An unresolved encounter is `removed` if its hazard was removed by the script
  (this takes precedence), otherwise `safe_hold` if the ego is stopped when the
  encounter ends (at the run end), otherwise `horizon_censored`. A stop earlier
  in the encounter does not make a safe hold.
- A demo handful of runs is illustrative only. Detecting a 10-point difference
  in failure rate needs on the order of 150 paired runs (McNemar's test).
- It is synthetic and short-horizon (60 s): the driver controls speed and may
  choose a bounded go-around maneuver in a known, clear adjacent lane before
  rejoining its fixed route.
