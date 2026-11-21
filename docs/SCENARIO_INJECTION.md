# Custom crash and hazard scenarios (#72)

The Scenario Lab in the **Driver** panel injects scripted traffic events into
the structured-state Gridlocked simulator. In the crash preset, two cars move
into a visible collision, become stationary obstacles, and are removed after
the configured clearance interval. Gemini controls the following ego car's
speed and can request a low-speed pass through a clear neighboring lane. Its
proposals, actual movement, and controller interventions are separate.

## Try the demonstration

1. Open **Traffic demo**, then **Gemini driver**.
2. Choose one of the six events below. Configure distance
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

| Event | Visible behaviour |
| --- | --- |
| Car crash ahead | Two cars collide, stop and become disabled. |
| Stalled vehicle | A car slows to a stop and becomes disabled. |
| Car cutting in | A car merges from the side, then continues at 3 m/s. |
| Sudden hard braking | A leading car brakes from 12 m/s to zero. |
| Slow-moving traffic | A leading car keeps moving at 2.5 m/s. |
| Two-car roadblock | Two stationary cars obstruct the route without colliding. |

Carbon and the random adversary can select all six types. Each still uses the
same attack limits, safety checks, observation and outcome calculation. Carbon's
choice is not a guaranteed rotation through every type.

The default traffic scene still has its authored signal program and replayed
background traffic. Those can also cause stops. Review the exact observations
and interventions before attributing a stop or recovery solely to the crash.
There are at most 60 model decisions and 60 simulated seconds. Gemini Decisions
selects a bounded speed action about once per second while motion continues.
A command expires three seconds after observation capture if its replacement
has not arrived. Wall-clock request latency and simulation-time response latency
are different quantities in the export; recorded response application times
identify when the selected action actually starts controlling the car.

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
is 1–4 seconds, and duration after the approach phase is 1–20 seconds. Moving
hazards continue during that interval. Removal is an explicit
scripted clearance operation, not a claim that a vehicle physically disappeared.
Actor IDs are derived as `event-<event-id>-<index>` and preallocated for rendering.
Unsupported schema/runtime/type/cleanup, extra fields, non-finite values and
out-of-bounds parameters are rejected. There is no Genie or SUMO injector here.
Routes too short for the configured ahead distance and moving hazard's maximum
travel distance are rejected before inference. The travel allowance is independent
of the chosen clearance duration, so admission cannot leak future cleanup timing.
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
It must not mutate the scene or make network/model calls. `contact` marks a
scripted background collision. Non-collision events default to a `disabled` log
record; moving or braking manoeuvres use `completionKind: 'maneuver_complete'`.
An optional `progressOffset(age, event)` moves the sampled pose along the actual
route, including turns, rather than along its initial tangent. All six built-in
events use the same driver loop. New parameter shapes require a scenario-schema version change; JSON import
does not execute arbitrary code or register executable event handlers.

## Trace, comparison and replay

**Export trace** in Scenario Lab downloads `gridlocked-driver-trace.v2`, which
extends the merged #60 action-trace workflow with the event and replay data:

- Synthetic provenance, live-model versus fixture label, run/scenario/route
  identity, scene seed and version, complete event plan and configured bounds.
- Each exact permitted observation, its simulation time, response model/request
  identifiers, proposed decision, response application time, and measured wall-clock request duration.
- Fixed-step applied frames, ego speed/progress, requested versus constrained
  target speeds, controller interventions and sampled oriented-footprint contacts.
- Requested/applied event times and frames, scripted collision/disabled/clearance
  records, and the first observation in which an event affected the model input.
- A real terminal reason: route completion, decision limit, time limit, interruption, reset,
  or error. An interrupted pending request has no fabricated response.

New completed exports include the compatible `gridlocked-driver-trace/2` action
trace under `driverTrace`. The existing **Download trace (JSON)** button still
downloads that compact format separately. Its effective leader/signal guards,
partial-hold finalization and redacted errors are preserved. Scenario Lab's
controller counts also include target-speed constraints and route shaping;
these differ from the compact trace's guards that changed applied motion.
Use the extended Scenario Lab export for **Import scenario / replay**; the
compact action trace does not contain recorded scene frames.

Exports include no gateway credentials or provider response bodies. Model
reaction means a braking/stopping, reduced-speed, or go-around proposal after receiving the
obstruction; local emergency stopping is counted separately. Scripted background
collisions are not counted as ego contacts. Contact metrics describe sampled
rectangle geometry, not realistic crash physics, damage, or road-safety validity.
Near-miss scoring is unknown. Go around can use known same-direction neighboring
lanes to pass a stationary obstruction and rejoin; changing the destination route
is unsupported. Street routes without lane geometry cannot offer this maneuver.
Injected-hazard and replayed-traffic ego contacts are counted separately. Existing
background traffic does not react to changed ego motion and can overlap a stopped
ego car; those sampled contacts remain in the export and are not automatically
scored as model failures. Reactive background traffic remains #19.

Completed/interrupted traces survive reset in the current tab (last four runs).
Export before page reload. Reset starts with fresh event state and a new run ID;
repeated manual triggers are idempotent. Live simulation cannot seek; recorded
replay can. Import validates replay version, geometry, actor identities, finite
state and frame ordering before using it. The current importer accepts both
historical extended v1 traces and continuous extended v2 traces, with recorded
playback bounded to 60 seconds. Replay is exact recorded playback;
fresh live-model calls need not reproduce the same decisions. Fixed simulation
steps reproduce state given the same applied events and decisions. Genie remains
stochastic and is not represented by this trace or repeatability claim.

## Adversary lane (#91)

The **Adversary lane** in the Driver panel chooses which registered event to
inject and when, instead of a hand-authored Scenario Lab plan. It reuses the
format, registry, `parseScenario`, `requestEvent` and trace above; there is no
second injector. Choose **Carbon**, **Random baseline** (with a seed) or **Off**
(the default; the demo is then unchanged), then start the drive as usual.

- **Decision moments.** The adversary is asked at simulation-clock barriers every
  3 s (0, 3, … s), at most 16 times per run. Carbon is `POST /api/adversary/attack`
  (the `adversary` gateway stage). While it answers, the continuous driver
  pauses physics, driver requests and command ageing, so Carbon latency never
  changes when an attack lands. Any adversary error (HTTP error, invalid or
  out-of-budget decision, timeout) stops the adversary for that run. There is
  no fallback attack, and the lane shows the error. The drive continues.
- **Budget, enforced in code before any call:** at most 2 attacks, at least
  10 s apart, triggered by 48 s, so 12 s of the 60 s run remain. Decisions are
  validated strictly (unknown type, out-of-bounds parameters and extra fields
  are rejected) and converted into a #72 event checked by `parseScenario`.
  Both requested triggers and their actual next-3-second application barriers
  must be at least 10 s apart; rounding must not shorten the gap.
- **Application.** Because #72 time triggers end at 24 s, each attack fills a
  preallocated slot (`attack-1`, `attack-2`) as a `manual` event. It is requested
  and applied at the first barrier at or after `trigger_seconds`. The record keeps
  both times.
- **Avoidability gate, the same for both adversaries.** With the ego's actual
  speed v at that barrier, an attack with `distanceAheadM < 3v + v²/7 + 5` is not
  injected. It is recorded as `void: unavoidable` and counted separately. Here 3 s
  is the maximum command age and 3.5 m/s² comfortable braking.
- **Random baseline.** It runs at the same barriers, with the same budget,
  cutoff, decision shape and choice-time avoidability gate as Carbon. It samples
  uniformly among admissible trigger tenths that satisfy both spacing checks,
  and draws distance only from the avoidable interval at the current speed.
  If no distance is admissible, it waits. Both policies still recheck actual
  speed at injection. The policy uses its own seeded mulberry32 stream, never
  simulation randomness; numeric parameters are rounded to tenths.
- **Eval option: leader guard ignores injected hazards.** The leader guard skips
  `event-*` actors, both in its target cap and in its hard-stop boundary. The
  model still sees them through `lead_gap_m`/`lead_speed_mps`, and the
  observation is unchanged. Go-around corridor, red-line and replayed-traffic
  guards still act.

**Per-attack records** (`trace.adversary.attacks`) are derived from the frames,
telemetry, contacts and event log:

- type, decision time, `trigger_seconds`, actual application time, who chose it
  and why;
- `outcome`, with precedence contact > near_miss (exact oriented-rectangle
  separation < 1.0 m) > cleared (the ego's rear has passed the attack anchor
  along the route and every obstruction corner lies behind its rear) > unresolved;
- for `unresolved`, a `resolution`: `safe_hold` (the ego stopped behind it),
  `removed` (scripted clearance came first) or `horizon_censored`;
- `guard_assisted`, true when any guard changed applied motion between
  application and resolution. A per-tick `guard` field was added to telemetry
  for this.

**Run-level metrics** (`trace.adversary.run`) sit beside these, so that a
stalling driver cannot look good:

- any contact, and incidents deduplicated across attacks;
- minimum separation;
- route progress;
- time stopped on a traversable route. This is a shortcut: green signal, no
  injected hazard present and route not finished. A stopped replayed leader
  still counts.

The lane reports unassisted physical clearances over all eligible issued attacks.
Assisted attacks and horizon-censored unresolved attacks remain in that shared
denominator. Near misses, safe holds and scripted removal earn no clearance credit.
The assisted/unassisted clearance rates are separate diagnostic slices, and all
physical outcomes are counted. Void exclusions and censoring are disclosed.
These are descriptive counts from attacks chosen during this run, not the
predeclared paired evaluation roster required by #32/#70. All of this is an
optional `adversary` section in the existing v2 trace; the version is unchanged.

Limits: contact and separation are sampled at 0.1 s ticks. There is no swept
contact between ticks. Crossing hazards (pedestrian, red-light runner) need the
dedicated hazard fields from #70 and are a follow-up.

## Verification

`npm test` exercises validation, visible collision/clearance, sideways obstacles,
observation isolation, manual trigger queuing, single application, fresh reset,
frame triggers, extension registration, controller attribution, baseline pairing,
interrupted/failed traces and replay import. Provider responses in tests are
fixtures, explicitly labeled. The live demonstration is recorded separately in
[SCENARIO_VERIFICATION.md](SCENARIO_VERIFICATION.md).
