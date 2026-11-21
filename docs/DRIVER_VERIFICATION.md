# Gridlocked simulator-driver verification

Verified locally on November 21, 2026. This evidence concerns structured-state simulation, not camera perception or the visual Genie navigation loop.

## Live Gemini run

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

The model chooses longitudinal speed. Steering and safety boundaries remain local deterministic code. Route speed limits and signals are not surveyed; mapped routes report unknown signals. The authored traffic surrounding the demo ego remains replayed and can fail to react to changed ego actions. This is not safety validation.

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

