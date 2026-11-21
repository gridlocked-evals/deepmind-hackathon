# Timestamped Hydrogen street evidence (#5)

This implementation uses the canonical `element119.v1` contracts, capture store,
shared model gateway and artifact store. Extraction and review do not require
a Genie session. The former separate `ObservationService` and `evidence_media`
implementation have been retired so the downstream pipeline consumes one schema.

## Inputs and limits

Extraction accepts an existing `captureId` and its exact `cameraId`. It reads
only that immutable capture's model-visible prefix. No inference or reinspection
fetches a new camera image. Captures stay pinned for review; release their pin
through the existing capture API when the evidence is no longer needed.

The source prefix is hash-verified. For continuous clips, ffprobe supplies the
actual decoded frame indices and presentation timestamps; times are normalized
to the first decoded prefix frame, not inferred from an assumed frame rate.
The decoded count must match the manifest. ffmpeg extracts by frame index.
Snapshots contain exactly frame 0 at media time 0.

The maximum prefix is 120 seconds. Each call receives at most six selected
original frames, with their indices, media times and hashes. Ownership advances
by six seconds, with up to two seconds of preceding context; a window is at
most eight seconds and a job at most twenty windows. This is sparse frame
inspection, not full-frame or continuous-motion coverage. Unsampled activity,
flow rates, metric geometry and persistent identities remain unknown.

Hydrogen observation calls use `thinking_level: low` and the gateway's normal
output allowance. The prompt/schema version is `hydrogen.street-evidence.v3`.
The provider receives a simplified wire schema with singleton constants retained
as enums. Full local validation still checks bounds, discriminators and semantics.

## Evidence table and downstream compatibility

The extraction report contains:

- Image-space anchors, lanes and crossings; visible actor-count intervals;
  signal states; obstructions; and derived, uncertainty-bounded visual events.
- Original source/capture/prefix hashes, original frame indices/timestamps,
  source-image ROI, any crop transform and exact bytes sent to the model.
- Explicit unknowns, abstentions, uncertain boundary events and duplicate events.
- One durable attempt record per window, provider and gateway request IDs,
  model configuration/input hashes, latency and available usage/cost estimates.
- Failed and cancelled attempts, unprocessed windows and an `optional_not_reviewed`
  label. Budget/auth/quota failures stop later windows instead of retrying them.

Every model frame reference must be among the frames actually supplied. Every
fact's time range must be inside both the inspected prefix interval and the cited
frames' time bounds. Source/capture/hash echoes must match. An invalid response
is retained as a failure and publishes no accepted observation batch.

Counts describe visible actors in one ROI at one cited frame; they are not
physical population estimates. Unobservable counts have null bounds. Unreadable
signals must use `unknown`. There is no state for physical disappearance: leaving
the image or becoming occluded does not establish that an actor ceased to exist.
Snapshot, repeated-still, stale-source and low-cadence jobs cannot publish events.

Temporal events require at least two cited continuous-media frames with distinct
original and model-image hashes; motion in uncited frames or outside a repeated
crop cannot establish an event. Their entire uncertainty interval must fit one
half-open owned interval (the final
interval includes its end). Boundary-crossing uncertainty is retained for review,
not counted as another event. Matching overlapping event intervals and regions
are conservatively deduplicated. This is an image-space heuristic, not an actor
identity tracker; nearby actors/events can remain ambiguous.

Each successful window writes one compatible `element119.v1` `ObservationBatch`
with that call's real `ModelCall`. It never attributes a multi-call aggregate to
one request. A richer `observation_evidence/<batchId>.json` sidecar is durably
written **before** the batch is published. It preserves typed quantities and all
evidence links. Core observation IDs join directly to sidecar rows; anchor IDs
use the same suffix with `anc_` instead of `ob_`. Existing Carbon edit code can
consume the core batch without a shared-contract migration.

Transport success alone does not qualify an observation for submission. The
gateway records qualification only after domain checks and durable batch/sidecar
storage, binding both records to their content hash. Fully abstained batches
remain bound and inspectable without qualifying as accepted evidence. Capture IDs
are stable episode identities; each window has its own attempt ID for cancellation
and failure accounting.

## Reinspection

An extraction permits at most two targeted reinspection attempts. The slot is
claimed before inference, including failed attempts. Each uses at most eight
seconds inside the parent's original interval and one normalized crop, with a
fixed focus (`counts`, `signals`, `anchors`, `obstruction`, or `events`). A child
cannot recursively create further inspections. Results do not overwrite the
parent or silently replace a failed attempt.

The exact rounded crop bounds are saved and output ROIs are mapped back to the
original image. Original pixels, crop bytes and both hashes remain available.

## API

All new routes require the existing backend `WORLD_API_TOKEN` as
`Authorization: Bearer ...`. Keep this token and model credentials on the backend.

| Route | Purpose |
| --- | --- |
| `POST /api/observations/extract` | `{captureId, cameraId, startS?, endS?}` over a saved prefix. |
| `GET /api/observations/extractions/{id}` | Full evidence table, failures and coverage. |
| `GET /api/observations/batches/{id}/evidence` | Rich evidence sidecar for a compatible core batch. |
| `POST /api/observations/extractions/{id}/reinspect` | `{focus, time: {startS,endS}, roi: {kind:"box",x,y,w,h}}`. |
| `GET /api/observations/extractions/{id}/review` | HTML facts table beside linked original frames. |
| `GET /api/observations/extractions/{id}/frames/{frameId}` | Original bytes; frames outside the job are refused. |
| `GET /api/observations/extractions/{id}/annotation-template` | Empty, hash-bound human annotation template. |
| `POST /api/observations/extractions/{id}/annotations` | Save human annotations and count/region/state/event comparisons. |
| `GET /api/observations/extractions/{id}/failure-report` | Failed, abstained and unprocessed windows without an invented accuracy score. |
| `GET /api/observations/extractions/{id}/reviews` | Separate historical review records; never pool repeated labels as independent samples. |
| `GET /api/observations/extractions/{id}/reviews/{reviewId}` | Verify a review's content hash and binding to this exact extraction. |

The review comparison preserves misses, model abstentions and count intervals
that disagree with human labels. Region IoU is reported. Event intervals must
overlap; non-overlap is a miss. Unannotated model facts remain unreviewed, not
automatically false positives. No human labels or accuracy scores are generated
by the model. The original review and every scored comparison are immutable.

Templates contain no labels and start with `reviewedOriginalPixels: false`. A
reviewer must explicitly attest that they inspected the original frames, supply
their name and `frameIds` for each annotation, and retain `extractionSha256` from
the template. `reviewMode` distinguishes independent raw-pixel review from review
assisted by the model table. The HTML shows original pixels first and keeps model
facts in a collapsed section. Count intervals are scored only for matching frames
and nearly identical regions (IoU at least 0.95); mismatched regions and human
unknowns remain explicitly unscored. Snapshot events and uncited frames are rejected.

Offline export and import use saved artifacts and make no model requests:

```sh
.venv/bin/python -m backend.observation_review --artifact-dir var/pipeline \
  --extraction extract_EXACT_ID --export-dir var/manual-review/new-packet
# Personally inspect review.html and original-frames.json, then save completed
# annotations under a new filename. The template remains empty until edited.
.venv/bin/python -m backend.observation_review --artifact-dir var/pipeline \
  --extraction extract_EXACT_ID --annotations var/manual-review/completed.json
```

Export refuses to overwrite existing files. A packet includes original-frame
metadata, self-contained HTML, an empty annotation template and a failure report.
Human review is optional and does not block extraction or downstream use. The
maintainer waived the remaining manual clip review; no human labels or accuracy
results are implied by that waiver. See [the verification record](OBSERVATIONS_VERIFICATION.md).

## Explicit live verification

Neither capture nor model calls occur without an explicit mode. From the project
directory on Windows (use the environment's Python executable):

```powershell
python -m backend.observation_live
# Save one real snapshot and approximately eight seconds of registered HLS media.
# HLS boundaries are whole segments; the actual duration is reported.
python -m backend.observation_live --capture-live
# Paid inference on those exact saved captures; no fresh camera requests.
python -m backend.observation_live --run-saved var/observation-verification/capture-selection.json
```

`--env-file PATH` can load an existing local configuration. When using a separate
worktree, point `GATEWAY_CONFIG` at the existing budget configuration and durable
ledger; do not create a fresh ledger to bypass spending limits.

The CLI exports an HTML review, an empty annotation JSON file, and a verification
report under ignored `var/observation-verification/`. Capture, inference retries
and human annotation are distinct operations. Read the [live verification
record](OBSERVATIONS_VERIFICATION.md) before claiming acceptance.
