# Verification record

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
