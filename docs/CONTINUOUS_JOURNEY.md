# Continuous NYC adversarial journeys

Open **NYC journey** in the top bar, then **Start journey**. The map follows the
car on an OpenStreetMap / OSRM driving route between NYC locations. Carbon chooses
hazards using the adversary from PR #97; Gemini Decisions controls speed. Steering,
acceleration, and the normal safety guards remain local simulation controls.

**Available scenarios (6)** lists Carbon's menu in the journey panel: car crash,
stalled vehicle, car cutting in, sudden hard braking, slow-moving traffic, and a
two-car roadblock. Carbon chooses the type and timing; it can repeat types or wait.
The two-attack limit applies per window, not to the number of available types.
To choose a particular hazard yourself, open **Traffic demo → Gemini driver →
Scenario Lab** and select it from **Event**. Moving hazards follow the route;
clearance removes them after the configured interval. These are scripted vehicle
behaviours with model-controlled selection and ego speed.

The journey automatically continues across 60-decision/60-second windows without
resetting the car's position or speed. On arrival it plans another route from the
actual arrival coordinate. If the car advances less than 2 metres for 25 simulated
seconds, it records a stuck restart and selects a different random NYC origin.
Route lookup failures stop the journey rather than inventing straight-line roads.

The panel shows the current trip, total distance, speed, driver decisions, restart
history, and Carbon's chosen scenarios with the existing per-attack outcomes and
guard-assistance flags. **Stop journey** cancels route, driver, and adversary work.
Hiding the browser tab stops the journey; returning does not silently resume API
calls. Starting a normal route, camera selection, or Traffic demo ends journey mode.

## Relationship to PRs #97 and #98

This feature builds on merged **#97**, which supplies `/api/adversary/attack`,
`adversaryBarrier`, the scenario injector, server-enforced attack budgets, and
outcome calculations. No second adversary endpoint or scoring implementation is
introduced. **#98** caches camera footage and applies evidence to camera-specific
drives; this journey uses synthetic hazards and does not claim to reconstruct them
from camera evidence.

The underlying adversary's simulation-clock barriers still pause motion while
Carbon thinks. This mode automates successive driving windows and trips; it is not
a change to #97's timing policy. Each window has a fresh run ID and the existing
two-attack limit. A window's unresolved hazards are recorded as horizon-censored
before being retired, rather than being relabeled as successfully avoided. The
next window retains the ego's progress and velocity. Stuck detection uses total
simulated journey time across windows, excluding inference pauses.

Gateway budget/auth/provider failures stop the whole journey. They do not trigger
a random restart or automatic paid retry in another window. Existing durable
gateway caps and the protected reserve remain authoritative; no budgets are raised.

**Export results** is available after stopping. The JSON includes counters, the
last 80 attack outcomes, last 40 restarts, and last four completed/interrupted
window traces. It is a bounded development record, not a statistically valid
adversarial benchmark. Window traces retain #97's model request IDs and outcomes.

## Validation

`src/journey/runtime.test.ts` runs the real continuous driver, adversary adapter,
physics and scenario injector with a fake clock and fixture API responses. It
covers continuation after 60 decisions, arrival continuity, a stuck restart,
visible hazard actors, cancellation during routing and Carbon inference, budget
failure without retry, and progress tracking across windows. Tests make no paid
provider calls.

Live browser verification on November 21, 2026 used a real OSRM NYC route, Carbon
and Decisions. The first trip applied a Carbon-chosen crash at 12 seconds and a
stalled vehicle at 24 seconds. The vehicle then triggered the 25-second progress
watchdog and restarted at another NYC location. The run was manually stopped on
trip two: 42 driving decisions, 42.9 simulated seconds, 64.0 metres, one stuck
restart. Both first-trip hazards remained `unresolved / safe_hold`; the crash
also recorded guard assistance. This is evidence of the UI/control flow, not a
claim that the defender independently avoided either hazard. The exported JSON
retained both window traces and the restart record. Four Carbon requests completed
with approximately $0.01933 in recorded adversary usage; no budget changes.

### Future hazard distance correction

A subsequent run exposed a planning problem: Carbon scheduled hazards 12 and
24 seconds ahead at distances of 25 m and 21 m while the car was slow. At
injection the car was travelling about 11.16 m/s, requiring approximately 56 m
under the avoidability rule, so both hazards were correctly skipped. Planning now
bounds speed at the scheduled injection barrier using the controller's 1.5 m/s²
maximum acceleration and 11.176 m/s speed limit. The prompt advertises an upward
rounded conservative distance (56.38 m when full speed is reachable); observed
speed stays separate. The server validates the chosen trigger's projected speed,
the random baseline uses the same planning envelope, and injection still checks
the actual speed. Distances are never silently clamped and the safety gate remains.

The live retest spawned a crash at 9 seconds and a stalled vehicle at 21 seconds;
both were visible, with neither marked void. The run was stopped after 25 driving
decisions. Outcomes remained unresolved safe holds, not claims of hazard clearance.
Regression coverage includes the reported 21/25 m cases and a stopped car
accelerating to 25 mph before a future hazard. Validation after integrating #97's
latest corrections: 178 frontend tests passed plus one existing expected failure,
246 backend driver/adversary/gateway tests passed, and the production build passed.

### Expanded scenario menu

The four additional vehicle events have regression coverage for motion, road
bends, driver visibility, duration isolation, route-end admission, cleanup and
recorded replay. Each also runs through the journey's real Carbon adapter and
injector with fixture API responses. The random baseline can draw all six types.

A live Carbon/Decisions run on November 21 selected a cut-in at 6 seconds (58 m
ahead) and hard braking at 18 seconds (60 m ahead). Both applied at the scheduled
barrier and rendered. The driver slowed behind the cut-in; it cleared at 23 s.
The run was manually stopped after 28 decisions, 28.7 simulated seconds and
119.4 m. Neither attack was void or guard-assisted. Outcomes were unresolved:
scripted removal for the cut-in and horizon censoring for hard braking. This
verifies generation and execution, not independent physical clearance. The
slow-traffic and roadblock types were verified with deterministic simulation
tests; all six choices were checked in the browser UI.
