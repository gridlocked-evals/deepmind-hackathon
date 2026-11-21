# Edit → reference → gate (issues #6, #7, #8)

```
ObservationBatch ─► Carbon StreetEdit ─► Nano Banana ReferenceImage ─► Decisions GateDecision ─► claim_world() ─► Genie (#9)
   (Hydrogen, #5)      backend/edits.py      backend/references.py         backend/gates.py            one session per accepted gate
```

All records are stored write-once under `var/pipeline/` (`PIPELINE_DIR`) by
[`backend/artifacts.py`](../backend/artifacts.py). Images and frames are
content-addressed blobs, re-hashed on every read. Every model call embeds an
`element119.v1` `ModelCall` with input hashes, latency, tokens and upper-bound
cost. Failed attempts are stored too.

## Carbon street edits (#6) — `backend/edits.py`

- **Input:** an `ObservationBatch`, a preset, the mayor request, optional permission narrowing, and the stored original frame ([`backend/frames.py`](../backend/frames.py), taken from the model-visible prefix and never the holdout).
- **Presets:** `protected_bike_lane`, `busway`, `plaza`. Each fixes its result class, the surfaces it may convert, the extras it may add, and required lane use (for example, a bike lane must allow `cyclist` and forbid `general_traffic`). `building_facade`, `sidewalk`, `crosswalk`, `pedestrian` and `signal` are never editable.
- **Carbon output:** typed operations with image-space polygons, cited anchors/observations and `becomes`. It also returns lane use, preserved anchors, unknown geometry, Nano Banana instructions, a Genie world prompt and a `reason`.
- **Validation in code** (`validate_output`) checks:
  - anchors and evidence IDs exist;
  - each operation region overlaps an editable anchored surface;
  - operations stay within the preset and the permissions, and the result class is actually created;
  - every observed crosswalk is preserved;
  - lane use has no contradictions;
  - there are no metric or safety/feasibility claims.

  A failure becomes a **visible** `rejected` edit (`validation: …`), and Carbon's raw output is kept.
- **Request checks before any model call:** contradictory permissions and unknown presets are refused with HTTP 422.
- **Unknowns:** every edit has a `metricGeometry` unknown. Carbon's unknowns (for example, occluded curb lines) are added to it.
- **Output:** the `EditBundle` (StreetEdit plus sidecar) is everything the image and world stages need.
- **Wire schema:** the provider rejects schemas whose length/range bounds make the decoding grammar too large ("too many states for serving"). `wire_schema()` strips those bounds from the schema *sent*; the response is still validated by the full Pydantic model.

## Nano Banana references (#7) — `backend/references.py`

- **Input:** the stored original frame, the edit's instructions, preserved anchors, the operations and a style.
- **Styles:** `photoreal` and `cel_shaded` are supported. `retro_16bit`, `claymation` and `video_game` require `allowExperimental`.
- **Normalization:** Nano Banana's 1K "16:9" output is 1376×768, which is not exact 16:9. Code center-crops it to exactly 16:9 (1360×765) and Lanczos-resizes to **1280×720**, the 16:9 size the Genie docs suggest and output. The raw output's hash and bytes are kept. Output more than 3% away from 16:9, smaller than 640×360, undecodable, or identical to the source is recorded as `malformed`.
- **Genie transport:** checked on the exact bytes: PNG/JPEG, exactly 1280×720, MIME type matches the bytes, and at most 8 MiB. The 8 MiB cap is our own local limit; the Genie docs give none for WebSocket. The 48 KB limit applies only to WebRTC, which isn't used.
- **Attempts:** at most 3 per edit and style (the first plus two gate-driven re-edits), serialized per edit and style. Each attempt has its own ID, hash, `ModelCall` and cost, including `failed` (gateway error) and `malformed` attempts. Nothing ever falls back to another camera or a cached image.
- **Cancellation:** a locally cancelled image attempt is recorded as failed before cancellation propagates and still counts toward the cap. Unreported usage stays unknown; it links to the actual gateway request when one was admitted, or has no request ID if cancelled while waiting for admission.
- **Labelling:** generated pixels, invented hidden surfaces and viewpoint changes are listed as `interpretations` on every record.
- **No world creation:** this stage never starts a Genie session.

## Decisions gate (#8) — `backend/gates.py`

- **Inputs:** one `gemini-decision-1` call per reference, given the SOURCE image, the EDITED image and the typed edit.
- **Questions** (atomic yes/no): `viewpoint_preserved`, `anchors_visible`, `requested_change`, `unambiguous`, and `crossing_continuity` when the evidence has crosswalks.
- **Thresholds:** versioned in [`backend/thresholds/reference_gate.v1.json`](../backend/thresholds/reference_gate.v1.json); the file's hash is recorded on every gate. An answer is `pass` if P(yes) ≥ `passAt`, `fail` if P(yes) ≤ `failAt`, and `unknown` otherwise or when confidence < `minConfidence`. **The current values are provisional** until they are calibrated on labelled development data.
- **Deterministic checks:** exact 1280×720 and transport validity of hash-verified bytes; provenance links to the edit, capture and frame hash; normalized coordinates; permitted targets. If any of these fail, the gate rejects *without calling the judge* (`modelCall.outcome = "blocked"`).

| State | When | Effect |
| --- | --- | --- |
| `accept` | every check passes | `allow_world_creation` |
| `re_edit` | only visual checks failed and attempts remain | blocked; feedback goes to the next Nano Banana attempt |
| `rejected` | a deterministic check failed or re-edits are exhausted | `block_world_creation` |
| `insufficient_evidence` | any `unknown` answer: uncertain, low confidence, or judge timeout/error | `block_world_creation`; the judge is **never re-sampled** |

- **One decision per image:** gating the same reference again returns the stored decision.
- **World binding:** `claim_world(reference, gate)` re-verifies the accepted image's bytes against the hash the judge saw and atomically claims the gate, so it admits one session. `bind_world(session, imageSha256)` then records which `WorldSession` used it, and `check_world_session` verifies the binding. Rejected and unknown gates raise HTTP 409.
- **Genie handoff (#9):** `publish_accepted(reference, gate)` (route `POST /api/worlds/accepted-references`, operator token required) re-runs `authorize_world` and publishes an `AcceptedReference` pinning the judged image hash and the exact gate record bytes. It spends nothing and does not claim. `WorldManager.create` re-checks the live gate record, creates the session (no paid request is sent until a client attaches), then calls `claim_world` and binds `world_<session id>` to the image hash. A gate that is rejected, unknown, already claimed or changed since publication fails before any Genie request; losing a claim race stops the new session before it can reach Genie.
- **Judge cancellation:** local cancellation persists an `insufficient_evidence` decision before propagating. Later callers receive that same decision instead of re-sampling the reference; any admitted gateway request remains linked without inventing usage.
- **What the scores mean:** they are a model's visual judgments. They are not crash probabilities, safety ratings or proof of geographic identity.

### Blinded human checks and calibration — `backend/gate_review.py`

```
python -m backend.gate_review export  var/review      # every gated attempt, shuffled; outcomes only in key.json
# reviewers fill sheet.csv: acceptable | unacceptable | unsure
python -m backend.gate_review score   var/review      # false acceptance + rejection/abstention coverage, Wilson 95%
python -m backend.gate_review calibrate var/review --target-far 0.1 --write backend/thresholds/reference_gate.v2.json
```

Calibration recomputes outcomes offline from stored judge answers, so it makes no judge calls. It writes a new read-only `frozen` thresholds file that records the hash of the labelled sheet.

## Routes

| Route | Returns |
| --- | --- |
| `GET /api/edits/options` | presets, styles, attempt cap, transport bounds, threshold version |
| `POST /api/observations` | stores an `ObservationBatch` (the write-in point for #5) |
| `POST /api/edits` `{observationBatchId, mayorRequest, preset, permissions?, frameTimeS?}` | `EditBundle` |
| `GET /api/edits/{id}`, `GET /api/edits/{id}/source-frame` | bundle; original frame bytes |
| `POST /api/references` `{streetEditId, style, allowExperimental?, feedback?}` | attempt record (502 if failed or malformed) |
| `GET /api/edits/{id}/references` | every attempt and gate for the edit |
| `GET /api/references/{id}`, `GET /api/references/{id}/image[?raw=1]` | record; normalized or raw image |
| `POST /api/references/{id}/gate` | `GateRecord` (idempotent) |
| `POST /api/edits/{id}/gated-reference` `{style}` | generate → gate → bounded re-edit loop with full history |
| `GET /api/gates/{id}` | `GateRecord` |

Gateway failures return the gateway's fixed public error: 503 not configured, 402 budget, 429 quota, 504 timeout, 502 otherwise.

## Live development harness

`python -m backend.pipeline_live [--camera ID] [--request TEXT ...]` runs a real DOT snapshot through Hydrogen, Carbon, Nano Banana and Decisions. Its Hydrogen step is a **minimal anchor stand-in**, labelled `hydrogen.dev-anchor-standin.v0`, so these stages can run on real pixels before #5 lands. It never creates a Genie session. Results are in [EDIT_REFERENCE_GATE_VERIFICATION.md](EDIT_REFERENCE_GATE_VERIFICATION.md).
