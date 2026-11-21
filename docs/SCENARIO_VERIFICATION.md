# Scenario injection verification — November 21, 2026

Issue #72 is assigned to `tasosger`. Implementation is on the isolated
`issue-72-crash-scenarios` branch. The live runs below used main through
`01dc3cf`; integration subsequently incorporated main through `5d813ef`,
including the Evidence workflow and #60 driver traces. These runs use synthetic structured state; they
are not camera perception, Genie worlds, learned adaptation or road-safety tests.

The live runs on this page predate the continuous Decisions migration and use
Hydrogen. Current Decisions-driver checks are recorded in
[DRIVER_VERIFICATION.md](DRIVER_VERIFICATION.md).

## Integrated verification before merge

- 106 frontend tests pass, including an interrupted crash fixture that checks
  matching observations, partial applied holds and effective obstacle guards
  across both trace formats, plus exact attribution for event IDs sharing a prefix.
- The full integrated backend suite passes: 500 passed, six skipped. The contract
  generation check and production build pass; the existing bundle-size advisory remains.
- The integrated browser loads successfully and exposes Traffic demo, the Gemini
  scenario controls and the upstream Evidence panel. Merge markers briefly broke
  the running development page during integration; they were resolved and the
  page was reloaded and checked before publishing.
- Upstream partial-hold export ordering, effective guard attribution, nested
  trace validation and error redaction are retained. Extended scenario exports
  embed the compatible action trace; both downloads remain available.
- No additional paid model calls were made for integration. The live results below
  describe the earlier implementation. Hard-boundary intervention accounting now
  follows the upstream rule that the boundary must change applied motion; the
  original exports and their historical counts remain unchanged.

## Earlier automated and browser verification

- 57 frontend tests pass. Added cases exercise validated import, visible
  two-vehicle contact and clearance, sideways obstacles, hidden event schedules,
  delayed manual triggers, idempotency, frame triggers, route exhaustion, fresh
  reset, extension registration, controller attribution, terminal traces,
  baseline comparison including time-zero injection, and replay validation.
- Before the scoped thinking-policy change, the backend suite passed 408 tests
  with four POSIX-only H100 cases skipped on Windows. After the change, the full
  suite passed 408 tests and had one existing three-second cleanup timeout in
  `test_storage_failure_still_cancels_provider`; that test passed on its isolated
  rerun. All 111 driver/gateway tests, including the new policy-scope test, passed.
  No cleanup-test timeout or runtime behavior was changed to conceal the failure.
- The production build passes with its existing bundle-size advisory.
- Desktop browser checks verified a real model-backed crash run, baseline run,
  trace/comparison download, invalid runtime rejection, custom manual stalled
  vehicle import, fresh-page trace import, replay seeking and disabled model
  driving during recorded replay. Browser console checks showed no errors.
- Credential scanning of tracked and unignored source files passed. Captures,
  screenshots, exported traces, local server logs and credentials remain ignored.

## Live attempts, including the failure

All attempts used the existing Gemini gateway, navigation-stage budget and shared
durable ledger. No cap was raised and no Genie session or demo reserve was used.

| Run | Run ID | Result | Calls | Estimated usage |
| --- | --- | --- | ---: | ---: |
| Initial event, default thinking | `2620d34f-3152-4e20-aa96-de090e0a661e` | Five valid decisions; sixth response invalid. Stopped at 15 simulated seconds. | 6 | $0.077901 |
| Baseline, low thinking | `61873e8b-72f4-4992-88a2-4aaf7b2d9dee` | Completed all 12 decisions / 36 simulated seconds. | 12 | $0.053769 |
| Event, low thinking | `8dd9889b-53d3-481a-8d62-ab205f20ed06` | Completed all 12 decisions / 36 simulated seconds. | 12 | $0.054367 |

Total: **30 provider attempts, 29 successful and one invalid output**, with
estimated usage **$0.186037**. This is local gateway accounting, not an invoice.
Both completed runs ended at the decision limit, not at the route destination.

The failed call (`1363c752-092a-47cc-93aa-fb078bdc6581`) used 4093 thought tokens
and only three output tokens within the 4096-token bound. The driver correctly
stopped. The follow-up uses `sim-driver-v2` and a scoped `thinking_level: low`
policy within the same allowance. Observation and visual-navigation policies
were not changed by this driver fix. The original failed trace and its gateway
usage records are retained separately; no failure was replaced with a success.

## Observed crash response

The two scripted cars appeared at 3 simulated seconds, reached the collision pose
at 5 seconds, and cleared at 13 seconds. The event affected the model's normal
lead-gap/speed observation at 3 seconds. Gemini proposed braking at 3, 6 and 9
seconds, then a stop at 12 seconds. The ego car reached the stopped threshold
at **10.4 seconds**. It later accelerated after the authored light became green
and exceeded 1 m/s at **30.7 seconds**. This recovery time includes waiting for
the scene's signal, not just clearance of the crash.

| Measurement | Baseline | Crash run |
| --- | ---: | ---: |
| Route progress | 138.6 m | 61.0 m |
| Scripted background crashes | 0 | 1 |
| Controller-intervention frames | 0 | 34 |
| Ego contact frames with injected hazards | 0 | 0 |
| Ego contact frames with replayed traffic | 0 | 25 |

The reaction latency is zero **simulation seconds from the first model input
containing the obstacle**: the simulator pauses while waiting for the model.
This is not zero wall-clock inference latency; each trace step records actual
request duration separately. The baseline/event comparison is descriptive. The
scene and event configuration match, but fresh model responses are stochastic.

The 25 contact samples in the crash run involve replayed `traffic-1-0` at
15.4–16.6 seconds and `traffic-1-3` at 26.6–27.7 seconds. Existing replay traffic
does not react to changed ego motion and can pass through the stopped car. These
contacts are retained and classified separately from the injected crash. They
are not automatically scored as driver failures. Reactive background traffic
remains #19. Contact counts use sampled oriented rectangles, not swept physics;
near-miss metrics are unknown and rerouting is unsupported.

## Saved evidence

Ignored local directory: `var/issue72/`.

- `failed-event.json` — SHA-256
  `3afefd8cc12ca12f04f1e7be6ee8c0460c1771dab17c798aa9d262dda9afbcd6`.
- `baseline.json` — SHA-256
  `74c7ea2d69573216082f08f856155b04d7fe83d4397edb5281d05493564c4602`.
- `event.json` — SHA-256
  `244ffd9cc9b858ad33779154362add59141469b65eb690cbae080fb370deab2a`.
- `comparison.json`, `ledger-evidence.json`, and the initial failure's ledger
  sidecar retain proposals, permitted observations, actual model request IDs,
  applied frames, timing, local interventions and all usage/outcomes.
- `crash-live.jpg` shows the initial live crash; `crash-replay.jpg` shows the
  successful run imported after a fresh page load and paused at six seconds.

The exports precede a presentation improvement that splits injected-hazard and
replayed-traffic contact totals, and an added initial-frame field for comparing
time-zero events. Their original bytes are unchanged; the table above derives
the contact split from their recorded actor IDs. The collision and model loop
were not rerun for those reporting changes.
