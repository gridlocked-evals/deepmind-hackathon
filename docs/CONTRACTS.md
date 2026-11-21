# Element 119 contracts (`element119.v1`)

Frozen shared contracts for the eight-hour submission pipeline (issue #2). They let the four lanes build against mocks without waiting for each other or for SUMO.

| Side | File |
| --- | --- |
| Backend (source of truth) | [`backend/contracts.py`](../backend/contracts.py) — Pydantic models; JSON Schema at `GET /api/contracts/element119` |
| Frontend | [`src/contracts/element119.ts`](../src/contracts/element119.ts) — types plus `validateVerticalSlice()` runtime checks |
| Shared fixture | [`contracts/fixtures/vertical_slice.v1.json`](../contracts/fixtures/vertical_slice.v1.json) — one synthetic end-to-end episode, validated by both test suites |

The fixture is synthetic. Its IDs, hashes and numbers are not measurements.

## Pipeline and ownership

```
Capture ─► ObservationBatch ─► StreetEdit ─► ReferenceImage ─► GateDecision(reference_image) ─► WorldSession ─► NavigationEvent* ─► GateDecision(navigation_window)* ─► EvaluationResult
 (A,#4)       (A,#5 Hydrogen)   (C,#6 Carbon)  (D,#7 Nano Banana)   (C,#8 Decisions)              (B,#9 Genie)   (B,#10 human/Hydrogen) (C,#11 Decisions)             (C,#13)
```

| Contract | Owning lane / issue | Producer | Main consumers |
| --- | --- | --- | --- |
| `Capture` | A — evidence/capture, #4 | recorder | every stage (IDs, media hash, duration) |
| `ObservationBatch` | A, #5 | **Gemini 4 Hydrogen** | Carbon, UI evidence panel |
| `StreetEdit` | C — reasoning/eval, #6 | **Gemini 4 Carbon** | Nano Banana prompt, UI before/after |
| `ReferenceImage` | D — images/UI, #7 | Nano Banana | reference gate, Genie, UI |
| `GateDecision` | C, #8 / #11 | Decisions API | world creation, navigation loop, UI |
| `WorldSession` | B — world/control, #9 | Genie | navigation loop, UI player |
| `NavigationEvent` | B, #10 | human or **Hydrogen** | temporal gates, evaluation |
| `EvaluationResult` | C, #13 / #14 | evaluation harness | submission evidence (#15) |
| `ModelCall` | C, #3 (gateway) | model gateway | embedded in every model-produced contract |

Hydrogen and Carbon are the two required qualifying Gemini 4 models. The validators require Hydrogen for observations and model-driven navigation, and Carbon for street edits. Nano Banana, Decisions and Genie cannot fill those roles.

## Shared rules

- **IDs** are `<prefix>_<4–64 of [A-Za-z0-9-]>`: `cap_`, `obs_`, `ob_`, `anc_`, `edit_`, `op_`, `ref_`, `gate_`, `world_`, `nav_`, `eval_`, `call_`. Camera IDs keep the existing registry form (`511ny-32998`).
- **Hashes** are `sha256:<64 lowercase hex>`. `Capture.mediaSha256` hashes the original media bytes. Hydrogen's `ModelCall.inputHashes` must include it, and the reference gate must hash the image it judged. Retries reuse the same immutable inputs.
- **Model provenance**: every model-produced object embeds a `ModelCall` with exact model ID, prompt ID, config ID, request ID when known, input hashes, request/response times, latency, usage, cost and outcome. Credentials never appear in contracts or logs.
- **Data provenance**: `observed` (seen in original media), `derived` (computed from observations), `assumed` (a stated assumption), `inserted` (mayor edit), `generated` (Nano Banana/Genie pixels).
- **Unknowns** are explicit `{field, reason}` entries instead of guesses. Gate checks may answer `unknown`, and evaluations count unknown outcomes separately (`succeeded + failed + unknown == attempts`).
- **Image space only**: regions are normalized `box` or `polygon` coordinates in the referenced image (x right, y down, in [0, 1]). Edits are scoped by anchors from the observation batch and by `permissions.allowed/forbidden` semantic classes. Metric geometry and physical route feasibility belong to the calibrated milestone (#16).

## Clocks

| Field | Meaning |
| --- | --- |
| `MediaTimeRange.startS/endS` | Seconds from the first frame of the captured media. Must lie within `Capture.durationS` (at most 120 s). |
| `Capture.retrievalStartedAt/EndedAt` | UTC time **we retrieved** the media. Not exposure time; `captureClock` is `unknown` for current DOT/511NY sources. |
| `NavigationEvent.sessionTimeS`, `GateSubject.sessionTime` | Seconds since the Genie session started streaming. |
| `ModelCall.requestedAt/completedAt`, `WorldSession.startedAt/terminatedAt` | UTC wall clock of our own requests. |

Snapshot captures have zero duration, so `motionSupported` must be false and they cannot carry `movement` observations.

## Gates are operational

| Subject | accept | reject | unknown |
| --- | --- | --- | --- |
| `reference_image` | `allow_world_creation` | `block_world_creation` | `block_world_creation` |
| `navigation_window` | `continue` | `terminate_session` | `flag_unknown` |

The outcome follows from the checks: any `fail` → reject, otherwise any `unknown` → unknown, otherwise accept. A `WorldSession` is valid only if it references an accepted reference gate.

## Genie is not a simulator

`WorldSession` is stochastic. It has `deterministicReplay: false` and rejects `seed`, `branchId` and other simulator identity fields. Deterministic seeds, branch IDs and frozen replays belong to the SUMO milestone (#19) and its `ScenarioBundle`/ScenarioIR contracts. They never appear in a Genie session.

## Routes

Implemented now:

- `GET /api/contracts/element119` — JSON Schema for every contract.
- `POST /api/observations`, `POST /api/edits`, `POST /api/references`, `POST /api/references/{id}/gate`,
  `POST /api/edits/{id}/gated-reference` and read routes — see [EDIT_REFERENCE_GATE.md](EDIT_REFERENCE_GATE.md) (#6–#8).
  `POST /api/edits` takes `preset` and optional `frameTimeS`; `/api/edits/{id}` returns an `EditBundle` wrapping the `StreetEdit`.

Proposed (owners implement against these shapes; none exist yet):

| Route | Returns | Issue |
| --- | --- | --- |
| `POST /api/captures` `{cameraId, maxSeconds}` | `Capture` | #4 |
| `POST /api/captures/{captureId}/observations` | `ObservationBatch` | #5 |
| `POST /api/worlds` `{referenceImageId, gateDecisionId, budget}` | `WorldSession` | #9 |
| `POST /api/worlds/{worldId}/terminate` `{reason}` | `WorldSession` | #9 |
| `POST /api/worlds/{worldId}/navigation` / `GET .../events` | `NavigationEvent` | #10 |
| `POST /api/worlds/{worldId}/gates` `{sessionTime}` | `GateDecision` | #11 |
| `GET /api/evaluations/{evaluationId}` | `EvaluationResult` | #13 |

## Changing a contract

Any breaking change bumps `SCHEMA_VERSION` (`element119.v2`), updates both validators and the fixture in one commit, and is announced on #2. Additive optional fields keep the version but still update both sides.
