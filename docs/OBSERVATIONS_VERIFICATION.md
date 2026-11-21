# Street evidence verification — November 21, 2026

Issue #5 is implemented on `issue-5-hydrogen-evidence`, using the merged
PR #35 capture/contracts/pipeline code with main through `e16b09f` merged.
The original #10 worktree and its known live-control issues were left intact.

## Automated verification

272 backend tests and 33 frontend tests pass after integrating main through
`e16b09f`. The production build passes with
the existing large-bundle warning. The 26 extraction tests cover real ffmpeg
frame-index decoding, schema constraints, wrong source/capture/hash/frame/time,
held-out suffix exclusion, snapshot motion/flow/identity rejection, abstention,
model failure accounting, cancellation, prefix pinning, crop transforms,
reinspection limits, overlapping event ownership, quota-stop coverage, human
review comparisons, API authentication and escaped HTML exports.

Provider behavior in those tests is scripted and test annotations are fixtures.
They do not count as human labels or successful live model runs.

## Real input captures

The captures were saved once at approximately 18:44:38 UTC. All subsequent model
attempts reused these exact saved prefixes. No inference retry refreshed the camera.

| Input | Capture | Actual original media |
| --- | --- | --- |
| NYC DOT snapshot | `cap_20261121T184438-28491952` | One 352×240 JPEG; source `1b3248f9-f26e-4f36-943f-b7a7731595fb`. |
| Registered 511NY clip | `cap_20261121T184438-a458371e` | 150 decoded frames over 10.01 seconds; source `511ny-32998`. |

The requested clip duration was eight seconds; whole HLS segments yielded 10.01
seconds, which was reported rather than silently relabeled. This is within the
120-second cap. Only six sampled frames per inference window were sent. Actual
camera exposure time is not established by server receipt time or a watermark.

Prefix hashes:

- Snapshot: `sha256:9daa9c9bb9ed09d2061e39a0928baf23cb49b3648cde42a2724e49b4e7610c3b`.
- Clip: `sha256:c4197f38ae7e525ecd7648ea4233d8a9164d7a2a5a48ac4b5afe482fdbde694d`.

## Attempts, including failures

| Version | Snapshot | Clip | Local usage estimate |
| --- | --- | --- | ---: |
| v1, default thinking | Abstained: empty fact list and four uncertainty notes. | Both windows incomplete: 4093 thought tokens and only 3 output tokens each. | $0.116944 |
| v2, low thinking | Rejected malformed `region.kind = bbox`. | First window rejected the same malformed discriminator; second yielded five accepted facts and one uncertain boundary event. | $0.098028 |
| v3, low thinking and corrected wire schema | Seven validated facts, two uncertainty notes. | Fifteen validated facts across two windows, five uncertainty notes. | $0.091316 |

There were **nine calls total**: four failed windows, one abstained window, and
four successful windows. All attempts and ledger traces remain available. Facts
from repeated runs are not pooled as independent samples. These were engineering
corrections to token allocation and schema transport, not threshold/quality tuning
against human labels. The simplified wire schema had removed `const`; v3 retains
fixed values as singleton enums and explicitly specifies `region.kind = box`.

Total new model usage estimate: **$0.306288**. The existing durable gateway ledger
was reused. No stage/global cap was raised, no Genie session was created, and the
demo reserve was not used. The local accounting total increased from $8.343308 to
$8.649596; it includes reservations from earlier unrelated work and is not a
provider invoice.

## Final validated run

| Input/window | Gateway request ID | Measured extraction-window latency | Estimated cost |
| --- | --- | ---: | ---: |
| Snapshot | `cdb6083c-f617-49bb-ab7e-886bd358006b` | 11,446 ms | $0.025173 |
| Clip, first window | `d091e91e-f4eb-4795-a575-d41fd4b5308b` | 14,022 ms | $0.032555 |
| Clip, second window | `aaef2b49-c33d-417e-8ea2-33d88b527d9c` | 11,059 ms | $0.033588 |

The snapshot produced lane/path regions, bridge and sign anchors, a visible
vehicle interval of 6–10 in its specified ROI, and a one-cyclist observation.
Unreadable/no-visible signals and occluded vehicles remained in uncertainty
notes. The clip produced facade/sidewalk anchors, image-space road regions,
foliage obstruction, visible-count intervals and one derived exit-from-view
event. These are model judgments, not manually confirmed ground truth.

Extraction reports:

- `extract_20261121T184918-e0e90dad` (snapshot), SHA-256
  `77f09162f4daa6a2e89a617a5a983790a02f9ca3669133e3ed12f5775512fb1c`.
- `extract_20261121T184929-43e238e3` (clip), SHA-256
  `0d641f285cb41ad3b5738363cd2764f489559250b6eebd867743f4fb4ac8afcd`.

Private artifacts under `var/observation-verification/` include immutable capture
manifests/media, original decoded frames, all six extraction reports, nine window
attempts, per-batch evidence sidecars, three verification summaries, HTML reviews,
and empty human-annotation templates. These files and credentials are Git-ignored.

## Remaining acceptance work

**Human development annotation is partial.** The maintainer replied
“arounf 7 vehicles” for the original snapshot. This is saved verbatim in a
hash-bound `human-feedback.v1` review record as an approximate estimate of seven,
with no invented uncertainty bounds or ROI. Seven falls within the model's
6–10 interval, but the model interval is for a specified left-roadway ROI;
the human response did not specify a matching region. The comparison therefore
remains unscored and is not an accuracy result.

The maintainer subsequently instructed: “forget snapshot quality.” Additional
snapshot quality review is deferred and no longer blocks the current delivery.
No further snapshot count-region, signal-color or fixed-feature annotations are
requested. The successful snapshot extraction and provenance checks remain
recorded; this scope decision is not a snapshot accuracy result. Original model
outputs, partial feedback and empty templates remain unchanged.

Clip count/event/anchor annotations remain pending. Complete that raw-pixel
review and its hash-bound annotations before tuning clip quality or declaring
the remaining manual-review acceptance work complete.

Reinspection bounds, event deduplication and holdout isolation pass automated
tests; live crop reinspection and a broad camera/cadence benchmark were not run.
The event deduplication is a conservative spatial/temporal heuristic, not actor
tracking. PR #35 and the capture pin/eviction fixes in PR #49 have merged into
main and are integrated into this worktree; that dependency is resolved.
