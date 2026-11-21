# Browser lifecycle regression tests

The Playwright suite exercises the built React app, MapLibre/Three renderer, driver hook, route planner, and deterministic physics in Chromium at desktop (1440 × 900) and mobile (390 × 844) viewport sizes. It needs no Python server, environment file, API key, camera connection, or model credit.

```sh
npm ci
npx playwright install chromium
npm run test:browser
```

On Linux, install browser system dependencies with `npx playwright install --with-deps chromium`. The runner builds the app, checks test TypeScript, and starts its own preview at `http://127.0.0.1:5191`. That port must be free: an existing server is deliberately never reused. Normal development servers can keep running on other ports.

For parallel checkouts, select a different free port with `PLAYWRIGHT_PORT=5193 npm run test:browser`.

After building, target one case with `npx playwright test --project=desktop-chromium -g 'late responses'`. Open retained failure diagnostics with `npm run test:browser:report`; inspect a trace with `npx playwright show-trace test-results/<case>/trace.zip`.

## Coverage

- Stop during a pending decision and during motion; restart with a new run identity.
- Late responses after Stop and after restart, including a transport that ignores AbortSignal.
- Camera selection, route replacement, and Traffic demo selection cancelling the previous run.
- Hidden/visible lifecycle events stopping motion without automatic resume or elapsed-time jumps.
- HTTP errors, the 130-second timeout, invalid JSON, invalid decision fields, and mismatched run/step identities, without a fallback decision or automatic retry.
- Replay → driver → replay and Map/Intersection/Follow perspective changes, comparing controls with the frame and ego transform actually consumed by the renderer.
- Perspective changes during a delayed basemap style replacement, without crashing the map or the driver.
- Twelve decisions maximum, at most three simulated seconds per decision, a frozen simulation while awaiting responses, and distinct destination/decision-limit completion states.
- Lifecycle and timing cases explicitly run the baseline without hazards, so short-route arrival remains valid with the scenario lab enabled. A separate event-mode case starts the primary scenario control, checks that the scheduled hazard reaches the next model observation, and confirms that a pending response still freezes simulation.

## Isolation and observability

`tests/browser/fixtures.ts` intercepts requests for the entire browser context. Camera lists, camera media, model availability, decision responses, OSRM routes, map style, fonts, and glyphs are local fixtures. Only the preview's static build assets pass through. Unexpected requests and WebSockets are blocked and fail the test; service workers are disabled. Route planning still selects cameras through the real UI and validates that the OSRM URL contains those selected coordinates.

The gateway holds real HTTP fetches until a test releases them. Cancellation tests selectively remove the decision fetch's signal while recording that the app supplied it. This forces the run-identity guard to reject a genuinely delivered stale response; a successful transport abort alone cannot make the test pass.

The suite controls browser timers using Playwright Clock. `runFor` advances every animation frame during driving. `fastForward` is used only while motion is stopped/pending, or for the request timeout; it must not be used to simulate driving because the physics bounds each animation delta.

The renderer publishes its last successfully drawn scenario, frame time, and ego world-transform coordinates on the canvas beside the existing rendering diagnostics. Assertions inspect those values in Intersection and Follow modes. City overview intentionally culls 3D vehicles at low zoom, so its retained diagnostics are not treated as a newly rendered frame.

Headless Chromium keeps background pages visible. The visibility tests explicitly override `document.hidden`/`visibilityState` and dispatch `visibilitychange`, then restore the native properties. This covers the app's browser lifecycle contract, not operating-system tab suspension. Live provider behavior, live camera quality, real basemap content, cross-browser compatibility, and visual screenshot comparisons remain outside this suite.

## CI and failures

`.github/workflows/browser-tests.yml` runs unit and browser tests on pull requests and pushes to main, with no provider secrets. Chromium uses software WebGL so a hardware GPU is unnecessary. CI uses one worker and halves the desktop rendering pixel density while preserving its CSS viewport and all physics/frame assertions. Mobile uses normal pixel density. This bounds software raster cost without changing the layout under test.

Failures retain screenshots, traces, and an HTML report; the first retry also records video. CI uploads reports and diagnostics for 14 days, including when a retry succeeds. Request history, browser errors, and unexpected-network diagnostics are attached to failing cases. Uncaught browser errors fail the test. A CI retry gathers diagnostic evidence, but flaky tests still fail the job. The suite stops after three failures or ten minutes so reports can flush before the job's fifteen-minute limit.
