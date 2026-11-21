# Element 119 contracts, version 1.0

The canonical models are in `backend/contracts.py`. `scripts/export_contracts.py` generates the frontend's `src/contracts/types.ts` and `schema.json`; `src/contracts/index.ts` validates untrusted JSON with that schema and the same semantic rules as Python. Existing `src/types.ts` remains the authored map demonstration's contract. Its simulator seed and metric lane geometry are not Genie session fields.

```python
from backend.contracts import ObservationBatch
observations = ObservationBatch.model_validate(payload)
```

```typescript
import { parseContract } from './contracts';
const observations = parseContract('ObservationBatch', await response.json());
```

Regenerate after editing the Python models, then run both test suites. CI or local checks can run `.venv/bin/python scripts/export_contracts.py --check` to detect drift. Semantic rules that depend on more than one field must also be updated in the frontend validator. Both languages consume the exact same successful fixture and invalid-case mutations in `fixtures/contracts/`.

The fixture is **synthetic test data**. Its mock trace IDs and outputs do not establish real model access, successful inference, accepted source imagery, or measured evaluation results.

## Shared semantics

- Every artifact carries `schemaVersion`, a local `id`, immutable `capture`, UTC `createdAt`, explicit `provenance`, nullable `trace`, and an `unknowns` list. Unknown values use `null` with an explanation where required, never fabricated zeros. Additional fields fail validation so an incompatible version cannot silently enter the pipeline.
- Local IDs contain letters, digits, `_`, and `-`, with 2–96 characters. Provider request/interaction IDs also permit `.`, `:`, and `/`, up to 256 characters. Local artifact IDs and provider session IDs are distinct.
- All timestamps are UTC ISO 8601 with `Z`; offsets from camera adapters must be normalized before validation. `receivedAt` is the server's retrieval time. `sourceCapturedAt` is separately nullable, with `sourceClock` equal to `exposure`, `program_date_time`, or `unknown`. Unknown clocks cannot claim an exposure timestamp or measured clock uncertainty. A known clock can still have unknown uncertainty.
- `CaptureContext.originalMediaSha256` hashes the exact original media bytes concatenated in capture order. The capture manifest retains each file's length and hash to identify boundaries and separately supplies `manifestSha256` to bind its metadata. These hashes must never be replaced with hashes of newly fetched bytes during inference retries. `captureId` and `sourceId` identify the captured episode and actual camera independently.
- `durationSeconds` is the total original source duration and cannot exceed 120 seconds. For video, `allowedInterval` is the half-open evidence-reference window `[startSeconds, endSeconds)` within those bytes; it may expose only the prefix of a longer capture. The endpoint is excluded so the first suffix timestamp cannot become prefix evidence. These bounds do not by themselves deduplicate temporal events across overlapping inference windows; downstream extraction must assign ownership separately.
- Snapshots have duration zero and `[0, 0]` as their allowed interval. They cannot contain temporal event observations or references to later frames. Repeated stills must not be relabeled as motion evidence.
- `ImageRegion` is a normalized top-left rectangle (`x`, `y`, `width`, `height`) inside `[0,1]`. Regions have positive area. No metric width, calibrated route, collision, or safety feasibility is implied.
- Each `EvidenceRef` must match its artifact's capture and original hash and fall inside its allowed interval. `frameIndex` may be null when unavailable. Generated-world frame hashes belong to `NavigationEvent.frameSha256`, not original-camera evidence.
- `ModelTrace` carries the exact model ID, stable gateway `requestId`, nullable actual `providerRequestId`, trace/prompt/config IDs, input hash, start/completion timestamps, outcome, and nullable token usage. Missing provider request IDs or usage stay unknown. A successful model trace is required for observations, edits, references, gates, and Hydrogen navigation; fabricated success is never a substitute for an unavailable service.

## Ownership and integration boundaries

These are proposed pipeline routes unless listed as implemented in `RUNNING.md`. Defining a contract does not implement the stage or claim its integration acceptance.

| Contract | Owner | Intended route | Required behavior |
| --- | --- | --- | --- |
| `ObservationBatch` | A: capture/evidence | `POST /api/observations` | Hydrogen extracts evidence-linked facts. Observation IDs are unique; invisible values abstain. |
| `StreetEdit` | C: reasoning/evaluation | `POST /api/edits` | Carbon returns `accepted`, `rejected`, or `needs_clarification`. Accepted edits include supported image regions, permissions, preserved observations, and separate image/world prompts. |
| `ReferenceImage` | D: images/UI | `POST /api/references` | Nano Banana returns generated image metadata and a server artifact ID, with a 16:9 aspect ratio. Raw source evidence is retained separately. |
| `GateDecision` | C: reasoning/evaluation | `POST /api/reference-gates` | Decisions checks apply explicit probability and confidence thresholds. Unknown blocks automatic world creation; any failed check rejects. |
| `WorldSession` | B: world/control | `POST /api/world-sessions`, `POST /api/world-sessions/{id}/complete` | References an accepted image/gate, retains provider interaction ID, declares transport and duration/cost limits, and records terminal reason. |
| `NavigationEvent` | B: world/control | `POST /api/world-sessions/{id}/actions` | Orders bounded human/Hydrogen/supervisor actions against generated frame hashes and the session clock. Release/terminate never holds keys. |
| `EvaluationResult` | C: reasoning/evaluation | `GET /api/evaluations/{id}` | Records dataset split, baseline, every attempt ID, unknown coverage, and measured-or-unknown metrics. |

`StreetEdit.regions[].permissions` assigns at most one access value per actor class. Duplicate/conflicting permissions are rejected. Preserved source observations remain immutable; inserted lane regions retain inserted/generated provenance. Metric geometry is a later contract.

Gate checks are phrased so a higher probability supports acceptance. Below `minimumConfidence`, or without a probability/confidence, the result must be `unknown`. Otherwise `probability >= threshold` is `pass`; lower probability is `fail`. All checks must pass for `verdict: accept` and `action: allow_world_creation`. The pipeline must execute this decision before opening a world; a schema alone cannot stop an out-of-band service call.

`WorldSession.replaySemantics` can only be `stochastic_no_exact_reset`. It has no simulator seed, branch ID, or deterministic replay promise. Closing a transport is not proof that generation ended; the world service must explicitly complete the provider interaction and then record the outcome.

`VerticalSlice` joins all seven contracts and checks capture identity, artifact links, unique IDs and navigation sequences, preserved-observation membership, accepted edit/gate prerequisites, and navigation timestamps against the session duration. Production handlers must resolve and check referenced persisted artifacts at each boundary, rather than trusting client-supplied capture or gate copies.

## Verification and remaining work

The shared fixture and malformed cases validate both runtimes, including malformed IDs, out-of-frame regions, conflicting permissions, source/hash mismatch, held-out references, excess duration, clock contradictions, missing required model roles, gate threshold contradictions, unrecognized simulator fields, and navigation/evaluation bounds. Separate snapshot tests verify that visible facts remain valid while temporal claims fail.

The gateway and capture store supply infrastructure for the next integration steps. Extraction, edit compilation, reference generation, operational gate handlers, playable sessions, navigation supervision, and real evaluation still need their respective issue implementations and live-service evidence. Event-only/reuse wording and baseline disclosure remain submission checks in the implementation plan.
