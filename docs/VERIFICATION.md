# Verification record

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
