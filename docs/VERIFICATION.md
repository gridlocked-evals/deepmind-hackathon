# Verification record

## Shared gateway and evidence workspace — November 21, 2026

Integrated the team's merged gateway/session runtime (`origin/main` at `6ec141c`)
with the capture/observation branch. One gateway now serves observations and worlds.
A temporary-ledger migration check imported all 13 existing attempts and preserved
$15.25 in conservative reservations and 207,977 reserved tokens, with the approved
$21 total ceiling, $5 demo reserve, and Genie disabled. The source ledger and `.env`
were unchanged; these local reservations are not an account balance or invoice.

Validation passed: **211 backend tests**, **48 frontend tests**, generated-contract
drift checks, and the production build. The existing large application-chunk warning
remains. Tests isolate developer credentials, storage and ledgers. New regressions
cover lost/corrupt retention pins, corrupt manifests and attempt records, prefix-only
VOD playback, sequence rollback, decoder time/allocation limits, deferred domain
acceptance, migration accounting, optional Genie, and camera/attempt UI races.

An isolated browser preview with model calls disabled verified:

- Real 511NY Vestry recording, actual buffered boundary selection, explicit stop,
  and sealing a 15.015-second clip containing 225 frames and 521,512 bytes. The
  retained VOD loaded with duration 15.015 and advanced through playback independently
  of the live feed and authored replay. Re-sealing the same interval retained the
  same original bytes in a separate capture artifact.
- A separate NYC DOT snapshot capture retained one original image (25,384 bytes),
  displayed its actual camera identity, and exposed snapshot-specific controls.
- Missing-key extraction produced an actionable error, preserved the capture, and
  exposed its failed attempt without automatically retrying. The isolated gateway
  records confirm **zero provider dispatches and zero charged usage** during review.
- Desktop layout and the 390 × 844 mobile drawer; no horizontal overflow at that
  width, and no browser console errors during the reviewed workflow. Original media,
  manifests and screenshots remain under ignored `data/ui-validation/`.

The Evidence drawer is a partial issue #12 implementation. The accepted-reference,
edit/image/gate/world UI sequence and independent human annotations/evaluation remain
pending with their assigned work lanes. No additional paid model calls were made,
and this verification does not establish held-out extraction accuracy or completion
of the full Mayor Mode pipeline.

## Live services and Hydrogen extraction — November 21, 2026

The primary Gemini project completed capability checks for all five configured services. Genie returned a video frame and acknowledged termination before the user requested that it become optional; it is now disabled with `GENIE_ENABLED=false`, including in default smoke batches. No further Genie calls were made. The Workshop project was not probed or used as an automatic fallback.

| Capability | Successful trace | Latency | Reported total tokens |
| --- | --- | --- | --- |
| Hydrogen | `trace-8e60c228-adb1-4031-88ff-6cdc24f53cd5` | 7.821 s | 274 |
| Carbon | `trace-a0ad7aab-7de6-4f15-8be4-f1c08d821dcf` | 20.423 s | 1,216 |
| Decisions | `trace-45d3a940-bc11-458a-8960-2b1152cb5540` | 0.273 s | 96 |
| Nano Banana | `trace-15f03630-9493-405e-968c-975f4c29bcb3` | 12.722 s | 2,326 |
| Genie | `trace-25c769bc-b2d8-4d70-8143-53c7a967dda5` | 5.738 s | 2,896 |

The integrated observation API then returned HTTP 201 for both retained real inputs and successfully retrieved their persisted artifacts:

| Input | Capture and artifact | Measured result |
| --- | --- | --- |
| 511NY Vestry video | `cap_73ad25cf6ce245a182ad9d010df99a19`; `observations_4e08cad932a64d008cdc8d48bcbf2d76` | 10.009 s declared / 10.008466 s decoded; 150 frames sampled to 11; 8 observations, 4 explicit unknowns; 19.747 s inference; 19,561 total tokens |
| NYC DOT snapshot | `cap_86e874e035164f38a5e9c1d791688aeb`; `observations_d1ccdd4682af4f67ad2be87e54c4651b` | One original image; 7 observations, 1 explicit unknown; 16.030 s inference; 6,161 total tokens |

The successful extraction traces are `trace-18fd77ca-18bc-496a-ba3d-7371565da885` and `trace-78a0e5fb-2cd5-43bb-8fcc-f02645a15e3c`. Source bytes/manifests, observation envelopes, attempts, and the private gateway ledger remain under ignored `data/`. Provider request IDs absent from stateless responses remain null; local request/trace IDs are not mislabeled as provider IDs. No accuracy scores or human ground truth have been established for these outputs.

All **13 provider attempts** remain recorded: 7 successful capability/extraction calls and 6 unsuccessful probes/diagnostics. Two tiny Hydrogen probes and one Nano Banana probe exhausted their output allowance. Two initial extraction requests and one diagnostic request hit the provider's schema-state limit. The fixes increased bounded smoke output allowances and simplified only the provider-facing schema; strict local field, bounds, and cross-field validation remain enforced. No incomplete result was accepted.

After explicit user approval, the local total reservation ceiling became $21, stage ceiling $7 / 131,072 tokens, retaining the $5 demo reserve. The ledger retained $15.25 in reservations; this is **not measured billing**. The video call exceeded its 16,384-token estimate, and the ledger retained the actual 19,561-token usage to reduce subsequent headroom. The current Hydrogen stage dollar allowance is exhausted. Quota maxima and invoice costs remain unmeasured.

The implementation passes **142 Python tests** and generated-contract drift checks. The tests cover actual ffmpeg/ffprobe timing and frame selection, snapshot restrictions, cropped/windowed evidence, provider schema projection plus strict local validation, persisted success/failure/restart recovery, tamper detection, concurrency/cancellation, and disabled Genie making no transport or budget reservation. Frontend code was unchanged in this tranche.

Remaining acceptance: independent human development annotations, quantified count/event/anchor errors and abstentions, temporal-event live validation, held-out baseline comparison, and the downstream edit/reference/gate/UI pipeline. Issues #3–#5 are not closed solely on these checks.

## Element 119 foundation — November 21, 2026

The shared Python/TypeScript contracts, model gateway, capture service, configuration, and integrated API pass **95 Python tests**, **35 frontend tests**, contract generation drift checks, and the production build. The existing large application-chunk warning remains. Provider HTTP/SDK tests use mocks and do not verify model availability or project quotas.

One actual public 511NY Vestry source (`511ny-32998`) was recorded and sealed after fixing parsing of the harmless `EXT-X-DISCONTINUITY-SEQUENCE` metadata tag. True discontinuity markers remain unsupported and are rejected. The accepted episode contained **4.671 seconds declared**, **4.670733 seconds decoded**, **70 frames**, and **159,988 bytes**. Its shared `CaptureContext` validated; exposure time and broadcast delay remained unknown.

- Capture ID: `cap_7f3295864f4c48ad83c50b77022394d4`
- Original-media SHA-256: `dcc84014d0a97a0d385e3c5408c1a753440fd069d2fdc1372d7c315ec6d990bf`
- Manifest SHA-256: `0b37c7fa537cfefa1d2cc9e39f1fb99de1d6b7621ed7c10f478ee6b22d976f8c`
- Retained local evidence: ignored `data/live-validation/` (not committed or publicly hosted).

This is narrow live-video acceptance, not a real snapshot/model/world/UI/evaluation result. No real model smoke calls were made; add the server credentials and run the explicit [gateway probes](MODEL_GATEWAY.md#capability-smoke-tests). Complete the remaining issue acceptance criteria before closing issues #3 and #4. The [contracts guide](CONTRACTS.md) documents the synthetic cross-language fixture and malformed cases.

## Baseline UI — November 19, 2026

The initial simulation UI was verified locally on November 19, 2026. The traffic scenario is deterministic, hand-authored illustrative traffic; camera footage is a separate public source.

## Automated checks

Run these from the repository root after following [the setup guide](../RUNNING.md):

```sh
npm run build
npm test
npm run test:api
```

The final pre-push run passed the production build, six Vitest tests, and 19 pytest cases. The build reports a large MapLibre/Three application chunk; the Python test client reports a dependency deprecation warning. Neither caused a failure.

| Suite | Coverage |
| --- | --- |
| Scenario | Seed reproducibility, geographic identity, deterministic seeking, bounded trajectories, lane separation, signal stops, interpolation across re-entry, unknown scenes, and cancellation |
| Camera API | Source identity, directory normalization/cache state, nested playlist and URI rewriting, camera-scoped tokens, expiration, proxy escape rejection, redirects, byte ranges, unavailable upstreams, and readiness |

## Browser checks performed

- Reviewed City, Intersection, and Follow perspectives at desktop size, plus the responsive layout at 390 × 844. The mobile page had no horizontal overflow.
- Observed Vestry video advancing through the local HLS proxy and confirmed source metadata and attribution were visible.
- Paused and scrubbed the demo while live footage continued independently.
- Switched to another camera and back to Follow without moving the Vestry scenario or resetting replay time.
- Disabled and restored the camera overlay: the video element count changed from one to zero and back to one without resetting the demo.
- Toggled traffic and route visibility, collapsed and reopened the camera list, and verified layer toggle state was exposed to accessibility APIs.
- Reviewed the production build served by FastAPI; no application console errors were observed in that review.

Chrome reported approximately 120 map render events per second in Follow with one video playing on the development machine. This is a local observation, not a benchmark or a guarantee for other devices. The renderer caps pixel ratio, instances traffic, batches static meshes, and suspends hidden-tab rendering.

## Repeatable manual review

After future changes, verify all three perspectives, pause/reset/seek, each playback speed, actor selection, layer toggles, camera switching, and feed collapse/hide/reopen. Confirm only one video element is active, and that changing the feed never resets the scenario clock. Check a live HLS source and a periodic snapshot separately.

Include keyboard-only navigation, reduced-motion transitions, hidden-tab/resume behavior, slow or unavailable feeds, and WebGL/map-style failure handling in broader acceptance testing. The initial browser review did not exhaustively force every system preference or failure path; the presence of fallback code is not a substitute for those checks.

Live source availability can change after verification. Capture timestamps and broadcast delay remain unknown, and the app does not interpret source availability as proof of real-time synchronization with the demo.
