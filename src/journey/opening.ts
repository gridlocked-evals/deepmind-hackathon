import { browserClock } from '../agent/continuous';
import type { DriverClock } from '../agent/continuous';
import { prepareDriverDecision, routePosition } from '../agent/physics';
import type { DriverSimulation, RoutePath } from '../agent/physics';
import { advanceEvents, createEvents, EVENT_RUNTIME, parseScenario, requestEvent } from '../scenarios/events';
import { createTrace, recordFrame } from '../scenarios/trace';
import type { RunTrace, ScriptedOpeningTrace } from '../scenarios/trace';

export const PASSING_SPEED_MPS = 3;
const DISTANCE_AHEAD_M = 28, PASS_OFFSET_M = -5, REJOIN_DISTANCE_M = 20;
const REQUIRED_ROUTE_M = 180;
export const OPENING_PLAN = parseScenario({ schemaVersion: 'gridlocked-scenario.v1', id: 'scripted-journey-opening', runtime: EVENT_RUNTIME,
  description: 'Scripted demonstration: slowly pass a crash, then a stalled vehicle. No model decisions.',
  expectedOutcomes: ['Show a slow lateral pass and return to the route before starting the next event.'],
  events: ['car-crash', 'stalled-vehicle'].map((type, index) => ({ id: `attack-${index + 1}`, type, trigger: { kind: 'manual' },
    parameters: { distanceAheadM: DISTANCE_AHEAD_M, approachSeconds: 2 }, durationSeconds: 20, cleanup: 'remove' })) });

/** Pick a straight stretch of the NYC route for this authored illustration. Lane availability is not inferred. */
export function openingStart(path: RoutePath) {
  const headings = path.points.slice(0, -1).map((point, index) =>
    Math.atan2(path.points[index + 1][1] - point[1], path.points[index + 1][0] - point[0]));
  for (let index = 0; index < headings.length; index++) {
    const start = path.distances[index];
    if (start + REQUIRED_ROUTE_M >= path.length) break;
    const heading = headings[index];
    let straight = true;
    for (let segment = index; segment < headings.length && path.distances[segment] <= start + REQUIRED_ROUTE_M; segment++) {
      const angle = headings[segment] - heading;
      if (Math.abs(Math.atan2(Math.sin(angle), Math.cos(angle))) > .12) { straight = false; break; }
    }
    if (straight) return start;
  }
  throw new Error('The scripted opening needs a straight 180 m street segment. Start a new journey to choose another route.');
}

const smooth = (value: number) => { const t = Math.max(0, Math.min(1, value)); return t * t * (3 - 2 * t); };
function offset(progress: number, anchor: number) {
  if (progress < anchor - 7) return PASS_OFFSET_M * smooth((progress - (anchor - 18)) / 11);
  return PASS_OFFSET_M * (1 - smooth((progress - (anchor + 8)) / 12));
}
function passingPose(path: RoutePath, progress: number, anchor: number) {
  const at = (distance: number) => {
    const pose = routePosition(path, distance), lateral = offset(distance, anchor);
    return { x: pose.x - Math.sin(pose.heading) * lateral, y: pose.y + Math.cos(pose.heading) * lateral };
  };
  const position = at(progress), before = at(progress - .01), after = at(progress + .01);
  return { ...position, heading: Math.atan2(after.y - before.y, after.x - before.x) };
}

export interface OpeningRun { state: DriverSimulation; trace: RunTrace; result: ScriptedOpeningTrace }
export function createOpening(initial: DriverSimulation, runId: string): OpeningRun {
  const progress = openingStart(initial.path);
  let state = { ...initial, time: 0, progress, speed: 0, braking: false, scriptedPose: undefined,
    events: createEvents(OPENING_PLAN, 'event') };
  state = prepareDriverDecision({ ...state, events: requestEvent(state.events, 'attack-1', 0) }) as typeof state;
  const result: ScriptedOpeningTrace = { controller: 'scripted-opening-v1', passingSpeedMps: PASSING_SPEED_MPS, complete: false,
    events: [{ id: 'attack-1', type: 'car-crash', appliedAt: 0, passedAt: null, rejoinedAt: null },
      { id: 'attack-2', type: 'stalled-vehicle', appliedAt: null, passedAt: null, rejoinedAt: null }] };
  const trace = createTrace(state, runId, 'scripted-demo');
  trace.scriptedOpening = result;
  return { state, trace, result };
}

/** Authored ego motion, explicitly separate from model control and evaluation scores. */
export function advanceOpening(run: OpeningRun) {
  if (run.result.complete) return run.state;
  const before = run.state, phase = run.result.events.find(event => event.rejoinedAt === null);
  const applied = phase && before.events!.applied.find(event => event.spec.id === phase.id);
  const target = applied && before.progress >= applied.anchorProgress - 20 ? PASSING_SPEED_MPS : 6;
  const acceleration = Math.max(-3.5, Math.min(1.5, (target - before.speed) * 1.8));
  const speed = Math.max(0, before.speed + acceleration * .1);
  const progress = before.progress + (before.speed + speed) * .05;
  const time = Math.round((before.time + .1) * 10) / 10;
  let state: DriverSimulation = { ...before, progress, speed, time, braking: acceleration < -.25, guard: 'none',
    scriptedPose: applied ? passingPose(before.path, progress, applied.anchorProgress) : undefined,
    control: { requestedTargetMps: target, constrainedTargetMps: target, interventions: ['scripted-opening'] },
    events: advanceEvents(before.events!, time, progress, before.path.length) };
  if (phase && applied) {
    if (progress >= applied.anchorProgress + 8 && phase.passedAt === null) phase.passedAt = time;
    if (progress >= applied.anchorProgress + REJOIN_DISTANCE_M) {
      phase.rejoinedAt = time;
      const next = run.result.events.find(event => event.appliedAt === null);
      if (next) {
        state = prepareDriverDecision({ ...state, events: requestEvent(state.events!, next.id, time) });
        next.appliedAt = time;
      }
    }
  }
  run.result.complete = run.result.events.every(event => event.rejoinedAt !== null
    && state.events!.log.some(row => row.eventId === event.id && row.kind === 'cleared'));
  run.state = state;
  recordFrame(run.trace, state);
  return state;
}

export function playOpening(run: OpeningRun, options: {
  signal: AbortSignal; clock?: DriverClock;
  onFrame: (before: DriverSimulation, state: DriverSimulation) => void;
  onPublish: () => void;
}) {
  const clock = options.clock ?? browserClock;
  return new Promise<DriverSimulation>((resolve, reject) => {
    let animation = 0, last = clock.now(), accumulator = 0, settled = false;
    const finish = (failure?: unknown) => {
      if (settled) return;
      settled = true; clock.cancelFrame(animation); options.signal.removeEventListener('abort', abort);
      if (failure) reject(failure); else resolve(run.state);
    };
    const abort = () => finish(new DOMException('Journey stopped', 'AbortError'));
    const tick = (now: number) => {
      if (settled) return;
      if (options.signal.aborted) { abort(); return; }
      accumulator += Math.max(0, Math.min(.25, (now - last) / 1000)); last = now;
      try {
        while (accumulator >= .1 - 1e-9 && !run.result.complete && !settled) {
          const before = run.state, state = advanceOpening(run);
          options.onFrame(before, state); accumulator -= .1;
        }
        if (settled) return;
        options.onPublish();
        if (settled) return;
        if (run.result.complete) { finish(); return; }
        animation = clock.frame(tick);
      } catch (failure) { finish(failure); }
    };
    if (options.signal.aborted) { abort(); return; }
    options.signal.addEventListener('abort', abort, { once: true });
    animation = clock.frame(tick);
  });
}
