# Hydrogen observations

`POST /api/observations` extracts a validated `ObservationBatch` from an existing immutable capture. It makes a real, budgeted `gemini-4-hydrogen` request when configured. It never captures a newer camera moment, accepts client-provided model traces, or opens held-out suffix media.

```json
{
  "captureId": "cap_existing_episode",
  "window": {"startSeconds": 0, "endSeconds": 10},
  "crop": null,
  "maxFrames": 32,
  "includeEvents": false,
  "ownedInterval": null
}
```

Omit `window` to inspect the full permitted prefix. A window must be inside that prefix; video time is half-open. Snapshot time remains exactly zero. A normalized `crop` optionally targets a region of the original image. At most 121 frames may be requested; the default is 32. The [evidence preparation guide](EVIDENCE_MEDIA.md) describes actual timestamp extraction, bounded sampling, byte limits, and crop rounding. The sampler covers the requested interval rather than silently dropping its tail, and reports the actual sampling gaps. Sparse frame sampling does not establish trajectories or calibrated flow.

The server supplies labeled inline image bytes and a JSON response schema through the documented [Interactions structured-output envelope](https://aistudio.google.com/docs/structured-output) and [inline image input](https://aistudio.google.com/docs/image-understanding#inline-image). Output allocation is 4096 tokens; incomplete output is a failure, not an accepted partial batch. The gateway retains actual usage and reserves the configured request budget before dispatch. Two observation jobs may preprocess or infer concurrently; additional requests return a typed `busy` failure before decoding media.

The provider schema retains types, required properties, enums, references, and nulls but omits numeric, string-length, and array bounds. The full nested schema exceeded the live service's grammar-state limit. Following its [schema simplification guidance](https://aistudio.google.com/docs/structured-output#simplifying-complex-schemas), configuration `observations-config-v2` keeps those bounds in the strict local Pydantic validator. The prompt requests at most eight useful facts; more than sixteen observations, excessive references, invalid numeric bounds, and long explanations still fail local validation.

## Accepted output and evidence

The response is the versioned `ObservationBatch` from [CONTRACTS.md](CONTRACTS.md). `X-Observation-Attempt-Id` identifies the corresponding attempt record.

- `GET /api/observations/{id}` retrieves the accepted artifact and checks its persisted integrity and original capture identity.
- `GET /api/observations/{id}/evidence` returns its attempt ID and supplied-frame audit, including original source times, frame hashes, crop regions, and source media indices. Original permitted bytes remain accessible through the capture media routes; frames never replace those originals.
- `GET /api/observations/attempts/{attemptId}` returns request bounds, actual evidence metadata, trace IDs, status, and sanitized failure codes. Gateway details remain in its ledger. Attempts are retained even when inference, validation, or caller cancellation fails.

The model returns frame references by the zero-based index of an image actually supplied in its request. It cannot choose a capture ID, hash, timestamp, provider request ID, or source provenance. The server resolves those fields, maps crop-relative ROIs back to the original image, and attaches the real gateway trace. Out-of-range frame indices and invalid image regions are rejected. A targeted artifact's allowed interval is narrowed to its requested window; it remains a verified subset of the original capture.

Positive observation values are rendered by the server from closed feature/state enums and optional count bounds. The provider cannot return arbitrary prose as a positive observation. This prevents snapshot outputs from encoding speed, trajectory, or flow-rate fields. Counts are instantaneous visible counts from exactly one supplied frame; they never accumulate across frames. Occluded or unreadable facts become null with an explicit explanation. This constrains the output format; it does not prove that the model correctly interpreted the pixels.

## Temporal event ownership

Static extraction is the default, including targeted reinspection of overlapping windows. To request temporal events, set `includeEvents: true` and an explicit `ownedInterval` inside the inspected video window:

```json
{
  "captureId": "cap_existing_episode",
  "window": {"startSeconds": 0, "endSeconds": 10},
  "includeEvents": true,
  "ownedInterval": {"startSeconds": 0, "endSeconds": 5}
}
```

Each accepted event requires at least two distinct supplied timestamps and its entire evidence interval must fit inside the half-open owned interval. Snapshot event requests are rejected. Event intervals owned by another active or accepted attempt cannot overlap, including after restart. Static reinspection can overlap freely. Duplicate observations within one response share a deterministic evidence key and are emitted once. Events crossing an ownership boundary fail validation instead of being counted twice; the caller should use a separately planned non-overlapping ownership partition or keep the output unknown.

The event ledger is scoped to the current single-process API service. On restart, unfinished attempts become `interrupted` and release ownership when no accepted artifact exists. If the artifact was persisted before the final attempt write, the service verifies its envelope and restores accepted status, preserving its event ownership and trace. Do not run multiple API worker processes against the same observation store until ownership admission is moved to a transactional shared store.

## Persistence and limits

Artifacts and attempt records are atomically written below the service's configured observation storage root (normally ignored `data/observations`). Accepted artifact envelopes checksum the artifact, evidence metadata, and attempt link together. Every attempt pins its source capture so later cleanup cannot silently replace or delete its evidence. The default attempt cap is 1000; admission then stops while retaining earlier records. Errors expose stable codes and attempt IDs, not raw provider messages, input image bytes, or credentials. No automatic capture retry or hidden model continuation occurs in this service.

## Verification and unfinished acceptance

Tests exercise server-owned traces/provenance, source bytes, crop mapping, window isolation, snapshot count-versus-event semantics, explicit occlusion unknowns, malformed model outputs, persistent event ownership, duplicates, saved failure attempts, API retrieval, preprocessing concurrency and cancellation. Evidence preparation tests additionally decode actual generated fixture media with ffmpeg/ffprobe.

These tests use synthetic fixtures and mocked inference. Real continuous-camera and snapshot runs must be recorded separately. Human development annotations for counts, events and anchors, measured extraction errors, abstention rates, and comparison against a baseline are still required for issue #5 acceptance. Neither schema validation nor a successful model response is a claim of observation accuracy.
