# Continuous NYC adversarial journeys

Open **NYC journey** in the top bar, then **Start journey**. The map follows the
car on an OpenStreetMap / OSRM driving route between NYC locations. Carbon chooses
hazards using the adversary from PR #97; Gemini Decisions controls speed. Steering,
acceleration, and the normal safety guards remain local simulation controls.

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

This feature is stacked on **#97**, which supplies `/api/adversary/attack`,
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
