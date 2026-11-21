# Gridlocked simulator-driver verification

Verification recorded on November 21, 2026. The Hydrogen run below predates the continuous Decisions driver and does not measure its latency. This evidence concerns structured-state simulation, not camera perception or the visual Genie navigation loop.

## Go-around choice

Two live synthetic-state calls verified prompt `sim-driver-decisions-v3` and the
conditional fifth choice. For a stopped obstruction 25 m ahead with a reported
clear right passing corridor, `gemini-decision-1` selected `go_around` at 4 m/s in
363 ms (`95cdab78-c64b-4159-b0f2-9b5ce343f16f`). With the same obstruction and no
passing corridor, it selected `brake` at 0 m/s in 305 ms
(`a8e977e2-aba7-4355-9517-f012d7e1da09`). These calls verify model choice and API
shape; maneuver geometry and collision handling are verified separately locally.

A six-decision live policy check used the authored Vestry state and a crash with
a 20-second clearance interval. Gemini cruised, accelerated, braked as the crash
formed, then selected `go_around` on the left at simulation time 5.0 seconds
(3.44 m/s, 19.55 m lead gap). That choice took 306 ms; request ID
`699407f3-4c42-4c63-9c6e-37f6c309430a`, run
`0e3db495-775b-43f7-908f-a7fe08f09dff`. The test then held the chosen command
locally for 15 seconds, reached a 4 m lateral offset, and rejoined without contact
with either crash actor. This was a synthetic-state policy/physics check, not a
full live browser drive or a test of command freshness.

Local checks passed: 135 frontend tests, 551 backend tests, production build, and
contract drift checks. One backend local-port test needed execution outside the
sandbox; the other 550 passed inside it. Passing regressions cover unknown/narrow
lanes, oncoming and rear-approaching traffic, occupied corridors, stops during a
pass, obstruction removal, full two-car crash clearance, rejoining, and red lights.

An isolated Chromium fixture at 1440 × 900 verified the rendered authored scene
over 18 continuous simulated seconds. Go around began at 5.0 seconds, reached
4 m left displacement, and returned to the route center while the crash remained
present. There were no sampled contacts with any actor. Independent oriented-box
checks of 112 passing frames found at least 0.735 m separating-axis clearance and
no road-corner violations. Both trace exports retained the action, and recorded
replay disabled driving and made no provider calls. A street route without lane
geometry kept passing unavailable, braked, and had no lateral deviation. Neither
browser phase had uncaught page errors. These browser responses were fixtures,
separate from the live Decisions calls above.

Integration with main `35e9399` preserved adversary and camera-evidence behavior.
The expanded suites passed 203 frontend tests, 678 backend tests, and 70 desktop/
mobile browser checks; production build, browser type checks, and contract drift
checks also passed. A regression verifies that an already stopped, model-commanded
hold is not credited as a maneuver-guard intervention, while blocked acceleration
is. The evaluation option disables only the injected-hazard leader guard; passing
corridor checks remain active.
After that attribution and label correction, all 10 affected desktop/mobile
adversary browser checks passed again against the rebuilt production bundle.

## Decisions driver migration

Three live calls through the updated `DriverController`, existing backend credential,
and durable gateway ledger returned `gemini-decision-1`:

| Structured scenario | Action and target | End-to-end controller latency | Gateway request ID |
| --- | --- | --- | --- |
| Clear route, starting from rest | Accelerate, 2.5 m/s | 1,273 ms | `ff823dee-1cbb-45e4-8473-fb9855580e3a` |
| Clear route, currently 6 m/s | Accelerate, 8.5 m/s | 217 ms | `1020cbe2-497e-48da-a068-4d1bafcd5d21` |
| Red signal 3 m ahead, currently 2 m/s | Brake, 0 m/s | 278 ms | `c42daf32-413c-4bfc-be64-99965629597f` |

These are three synthetic-state checks, not a general latency benchmark or a live
browser driving run. The first call includes connection setup. The updated browser
uses one continuous physics loop, a one-second request interval, and a three-second
observation freshness bound. Stop, failures and expired commands terminate the loop.

Migration checks: 100 frontend tests passed, production build passed, and contract
drift checks passed. The backend suite passed 521 tests inside the sandbox; its
remaining local-port binding test passed outside the sandbox (522 total). The
existing MapLibre/Three bundle-size advisory remains. Driver regressions cover
motion during pending inference, stale command expiry, late-result rejection,
route/time/decision limits, asynchronous trace attribution, typed Decisions output,
and deadline cleanup through the real gateway adapter with a fake HTTP provider.

Isolated Chromium checks at 1440 × 900 and 390 × 844 used intercepted provider
responses. While the second decision was held, the timeline advanced from 1.0 to
1.8 seconds with only one request pending. Stop at 2.3 seconds froze motion and
exported two contiguous applied intervals; the delayed observation preceded its
application time. A separate stop at 1.1 seconds ignored a late successful response
even when transport cancellation was disabled. A stalled request stopped at about
three seconds with a visible error and an error trace. Both viewports had no
uncaught browser errors. These browser checks used fixtures, not live model calls.

### Integration with crash scenarios

Before opening the PR, current main `5da39ee` added Scenario Lab. Its event
injection, fixed 0.1-second physics ticks, baseline comparison, recorded frames,
and replay remain connected to the continuous driver. Manual events requested
during inference are applied before the next observation snapshot. Extended
traces now use `gridlocked-driver-trace.v2` with Decisions policy metadata and
`appliedSimulationTime`; historical extended v1 recordings still import for replay.
Late responses cannot mutate finalized traces. The integrated frontend suite
passes 119 tests; the full backend suite passes 523 tests. Contract checks and
the production build pass. Replay coverage includes a 60-second, 601-frame run.

The merged build passed the desktop/mobile browser fixtures again. A manual event
queued at 1.0 seconds during a pending request applied once at the next snapshot,
2.0 seconds. The next model observation included the obstacle; its delayed brake
response applied at 2.6 seconds. Finalized extended exports were unchanged after
a late response. Scenario v2 export, replay and re-import made no new provider
calls, and model controls were disabled during replay. Both viewports had no
uncaught browser errors; the continuous-motion, Stop and expiry checks also passed.

## Historical Hydrogen run

The browser called the existing Hydrogen gateway through `POST /api/driver/decision`. The server used its existing backend-only credential and durable usage ledger. No mock decision or substitute replay was used for the controlled ego.

- Model: `gemini-4-hydrogen`.
- Completed run: `a29eefa9-7032-4911-9008-4a4e6d762bb5`.
- Selected route: West Street at Vestry Street → Hudson St at Laight St, using OSRM's returned driving geometry. The service returned a 4.48-mile route; this was not a straight-line preview.
- All 12 decisions succeeded. The browser advanced exactly 36 simulated seconds and stopped at the decision limit, with 6,915 m remaining from an initial 7,195 m. This was a bounded route segment, not arrival at the destination.
- Gemini requested acceleration from rest, cruising, braking before an approximately 88-degree turn, then acceleration after it. Target speeds and explanations appeared in the decision history; the ego's location/speed followed those targets through deterministic route steering and physics.
- Recorded provider latency was 3.04–6.23 seconds per decision. Simulation time froze while requests were pending.
- Ledger estimate for this 12-decision run: $0.059936 (the configured conservative rate calculation, not a provider invoice).
- First request: `c6aa7f23-9e08-46be-9fae-e0cd102f2d22`; last: `e124fbee-b312-4456-86d4-3bb9ddeb3906`.

An earlier authored-Vestry run produced three successful model responses during UI integration. A subsequent restart/Stop check produced one dispatched response: after the response arrived, the browser stayed at **Drive stopped**, 0 decisions, 0 simulated seconds. Stopping invalidates late responses; it cannot undo an already-dispatched provider charge.

## Local checks

- `npm run build` passed. Vite retains the existing large main-bundle warning from MapLibre/Three.
- `npm test`: 44 tests passed after integrating main through `b983633`.
- `.venv/bin/python -m pytest backend -q`: 350 tests passed after integrating main through `b983633`.
- Driver checks cover speed/acceleration/braking bounds, signal and lead-vehicle constraints, route completion, stale run identities, malformed responses, strict API schemas, bounded admission, idempotency, and redacted failures.
- Browser checks covered route search/planning, route playback → Gemini control, actual decision history, the 12-decision stop, restart/Stop with a late response, return to replay, and desktop/mobile layout at 1440×900, 390×844, and 731×727.

## Merge integration checks

After integrating main through `0d04e04` on November 21, 2026:

- `npm test`: 44 passed; `npm run build` passed with the existing bundle-size advisory.
- Full backend suite on Windows: 394 passed, 4 skipped. The skips are the POSIX-only H100 shell-wrapper cases; Python benchmark checks ran.
- Browser checks at 1240 × 667 and 390 × 844 covered the traffic demo, a 0.44-mile Vestry-to-Houston OSRM route, route playback, and driver initialization on the selected route.
- An isolated backend with model calls disabled returned the expected gateway error. The UI stopped at zero decisions and simulation time zero, then returned to replay. No live model calls were repeated in this review.
- The mobile tools panel contained keyboard focus, wrapped Shift+Tab, closed with Escape, and restored focus to its opener. Document width stayed at 390 pixels without horizontal overflow. No browser console errors were observed.
- `git diff --check` passed. The earlier live-run evidence above remains separate from these integration checks.

## Limits and next work

The model chooses speed or an available low-speed go-around maneuver. Steering and safety boundaries remain local deterministic code. Route speed limits and signals are not surveyed; mapped routes report unknown signals and lack the neighboring-lane geometry needed for passing. The authored traffic surrounding the demo ego remains replayed and can fail to react to changed ego actions. This is not safety validation.

- [#19](https://github.com/gridlocked-evals/deepmind-hackathon/issues/19): reactive traffic/physics.
- [#60](https://github.com/gridlocked-evals/deepmind-hackathon/issues/60): portable decision trace export with synthetic provenance.
- [#61](https://github.com/gridlocked-evals/deepmind-hackathon/issues/61): automated browser lifecycle regressions.
- [#10](https://github.com/gridlocked-evals/deepmind-hackathon/issues/10): separate visual Hydrogen → Genie navigation loop.
## Scripted browser trace export, November 21, 2026

The merged trace UI was exercised against a local ASGI fixture with model credentials
absent and providers disabled. All driver responses were labelled `fixture-no-provider`;
this was not live Gemini acceptance. A stopped six-decision run exported
`stopped_by_user`, synthetic simulator provenance, exact observations/decisions/request
IDs matching the fixture log, and the final partial hold from 15 to 16.4326 simulation
seconds. A scripted first-request failure exported `error` with zero steps. Returning
to replay removed the export control. Both JSON downloads were parsed and checked.

The trace-correction follow-up passed 80 frontend tests, the production build, and
contract drift checks. A malformed-response browser fixture returned an HTML body
with a test canary. Both the panel and downloaded trace showed only the fixed
"The driving request failed." message; the trace contained zero steps and no canary.
No provider was called.
