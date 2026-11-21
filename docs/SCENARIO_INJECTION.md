# Custom crash and hazard scenarios (#72)

The Scenario Lab in the **Driver** panel injects scripted traffic events into
the structured-state Gridlocked simulator. In the crash preset, two cars move
into a visible collision, become stationary obstacles, and are removed after
the configured clearance interval. Gemini controls the following ego car's
speed. Its proposals, actual movement, and controller interventions are separate.

## Try the demonstration

1. Open **Traffic demo**, then **Gemini driver**.
2. Choose **Car crash ahead** (default) or **Stalled vehicle**. Configure distance
   ahead, clearance duration, and a simulation-time or manual trigger.
3. Choose **Start scenario with Gemini**. The default event applies at 3 seconds,
   animates for 2 seconds, and clears 8 seconds after impact (at 13 seconds).
4. Inspect the event status, decision history and run summary. A manual trigger
   queues until the next decision boundary; it never changes an outstanding
   model request's observation underneath that request.
5. Export the trace. Run the same configuration with **Run baseline without
   hazards** to make a descriptive comparison. **Export comparison** includes
   both runs when the initial scene, configuration and provenance match.
6. Use **Replay recorded run**, or import its JSON later, to replay the exact
   recorded frames without making model calls. The simulation timeline seeks
   this recording; seeking does not re-inject an event.

The default traffic scene still has its authored signal program and replayed
background traffic. Those can also cause stops. Review the exact observations
and interventions before attributing a stop or recovery solely to the crash.
There are at most 12 model decisions, with 3 simulated seconds per decision.
Simulation time pauses while waiting for Gemini. Wall-clock request latency and
simulation-time response latency are different quantities in the export.

## Versioned scenario format

```json
{
  "schemaVersion": "gridlocked-scenario.v1",
  "id": "car-crash-demo",
  "description": "Car crash ahead",
  "runtime": "gridlocked-structured-v1",
  "events": [{
    "id": "hazard-1",
    "type": "car-crash",
    "trigger": {"kind": "time", "seconds": 3},
    "parameters": {"distanceAheadM": 35, "approachSeconds": 2},
    "durationSeconds": 8,
    "cleanup": "remove"
  }],
  "expectedOutcomes": ["Brake or stop; resume after clearance."]
}
```

Triggers are `manual`, `time` (0–24 seconds), or `frame` (integer 0–240 at
0.1-second physics steps). Files may contain one to four events with unique IDs.
Distances are 20–100 m ahead of ego **when the event applies**, approach animation
is 1–4 seconds, and stationary duration is 1–20 seconds. Removal is an explicit
scripted clearance operation, not a claim that a vehicle physically disappeared.
Actor IDs are derived as `event-<event-id>-<index>` and preallocated for rendering.
Unsupported schema/runtime/type/cleanup, extra fields, non-finite values and
out-of-bounds parameters are rejected. There is no Genie or SUMO injector here.
Routes too short for the configured ahead distance are rejected before inference.
If a later trigger runs out of road, the trace records `not_applied_route_end`
once; it does not silently reposition the event behind ego or count it as avoided.

The event's expected outcomes and future schedule remain evaluator-only. The
existing model observation includes the current nearest obstruction's gap and
speed; it does not receive an event name, crash schedule or expected decision.
Renderer and driver constraints sample the same injected actor states. Injected
obstacle footprints account for heading, so a sideways car is not discarded by
the normal same-route/same-heading lead-vehicle filter.

## Extension boundary

`src/scenarios/events.ts` owns the registry. Register a trusted, pure event
implementation with `registerEvent(type, {label, actorCount, contact, sample})`.
The sampler receives elapsed event time, the validated applied event and its
route pose. Return up to the declared one or two actor states with derived IDs.
It must not mutate the scene or make network/model calls. `contact` specifies
whether the scripted terminal event is a background collision or a disabled
vehicle. `stalled-vehicle` is the second implementation and uses the same driver
loop. New parameter shapes require a scenario-schema version change; JSON import
does not execute arbitrary code or register executable event handlers.

## Trace, comparison and replay

**Export trace** in Scenario Lab downloads `gridlocked-driver-trace.v1`, which
extends the merged #60 action-trace workflow with the event and replay data:

- Synthetic provenance, live-model versus fixture label, run/scenario/route
  identity, scene seed and version, complete event plan and configured bounds.
- Each exact permitted observation, its simulation time, response model/request
  identifiers, proposed decision, and measured wall-clock request duration.
- Fixed-step applied frames, ego speed/progress, requested versus constrained
  target speeds, controller interventions and sampled oriented-footprint contacts.
- Requested/applied event times and frames, scripted collision/disabled/clearance
  records, and the first observation in which an event affected the model input.
- A real terminal reason: route completion, decision limit, interruption, reset,
  or error. An interrupted pending request has no fabricated response.

New completed exports include the compatible `gridlocked-driver-trace/1` action
trace under `driverTrace`. The existing **Download trace (JSON)** button still
downloads that compact format separately. Its effective leader/signal guards,
partial-hold finalization and redacted errors are preserved. Scenario Lab's
controller counts also include target-speed constraints and route shaping;
these differ from the compact trace's guards that changed applied motion.
Use the extended Scenario Lab export for **Import scenario / replay**; the
compact action trace does not contain recorded scene frames.

Exports include no gateway credentials or provider response bodies. Model
reaction means a braking/stopping or reduced-speed proposal after receiving the
obstruction; local emergency stopping is counted separately. Scripted background
collisions are not counted as ego contacts. Contact metrics describe sampled
rectangle geometry, not realistic crash physics, damage, or road-safety validity.
Near-miss scoring is unknown and rerouting is unsupported in this speed-only MVP.
Injected-hazard and replayed-traffic ego contacts are counted separately. Existing
background traffic does not react to changed ego motion and can overlap a stopped
ego car; those sampled contacts remain in the export and are not automatically
scored as model failures. Reactive background traffic remains #19.

Completed/interrupted traces survive reset in the current tab (last four runs).
Export before page reload. Reset starts with fresh event state and a new run ID;
repeated manual triggers are idempotent. Live simulation cannot seek; recorded
replay can. Import validates replay version, geometry, actor identities, finite
state and frame ordering before using it. Replay is exact recorded playback;
fresh live-model calls need not reproduce the same decisions. Fixed simulation
steps reproduce state given the same applied events and decisions. Genie remains
stochastic and is not represented by this trace or repeatability claim.

## Verification

`npm test` exercises validation, visible collision/clearance, sideways obstacles,
observation isolation, manual trigger queuing, single application, fresh reset,
frame triggers, extension registration, controller attribution, baseline pairing,
interrupted/failed traces and replay import. Provider responses in tests are
fixtures, explicitly labeled. The live demonstration is recorded separately in
[SCENARIO_VERIFICATION.md](SCENARIO_VERIFICATION.md).
