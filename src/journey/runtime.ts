import { runContinuousDriver } from '../agent/continuous';
import type { DriverClock } from '../agent/continuous';
import { createDriverSimulation, driverFrame } from '../agent/physics';
import type { DriverSimulation } from '../agent/physics';
import type { DriverObservation, DriverResponse } from '../agent/types';
import { createRouteScenario } from '../navigation/routeScenario';
import type { NavigationPoint, PlannedRoute } from '../navigation/types';
import { adversaryBarrier, ADVERSARY_PLAN, attackRecords } from '../scenarios/adversary';
import type { AttackRecord } from '../scenarios/adversary';
import { createEvents, withEventActors } from '../scenarios/events';
import { beginDecision, createTrace, finishTrace, recordFrame } from '../scenarios/trace';
import type { RunTrace, ScriptedOpeningTrace, Terminal, TraceStep } from '../scenarios/trace';
import type { ScenarioBundle, SimulationFrame } from '../types';
import { ProgressWatch, randomStop, STUCK_SECONDS } from './route';
import { createOpening, playOpening } from './opening';

export interface JourneySnapshot {
  trip: number; restarts: number; windows: number; distanceM: number; seconds: number; decisions: number;
  speedMps: number; routeLabel: string; phase: 'routing' | 'opening' | 'driving' | 'thinking'; driverAction: string;
  opening: ScriptedOpeningTrace | null;
  attacks: (AttackRecord & { window: number })[];
  restartsLog: { trip: number; atSeconds: number; reason: string }[];
  trace: RunTrace | null;
}
export const emptySnapshot = (): JourneySnapshot => ({ trip: 0, restarts: 0, windows: 0, distanceM: 0, seconds: 0,
  decisions: 0, speedMps: 0, routeLabel: '', phase: 'routing', driverAction: 'Waiting', opening: null, attacks: [], restartsLog: [], trace: null });

export interface JourneyOptions {
  signal: AbortSignal;
  route: (from: NavigationPoint, to: NavigationPoint, signal: AbortSignal) => Promise<PlannedRoute>;
  driver: (run: string, step: number, observation: DriverObservation, signal: AbortSignal) => Promise<DriverResponse>;
  adversaryUrl: string;
  onScene: (scene: ScenarioBundle, frame: SimulationFrame) => void;
  onFrame: (frame: SimulationFrame) => void;
  onUpdate: (snapshot: JourneySnapshot) => void;
  onTrace: (trace: RunTrace) => void;
  random?: () => number;
  uuid?: () => string;
  clock?: DriverClock;
  /** The UI always includes the authored opening; false isolates live-agent tests. */
  scriptedOpening?: boolean;
}

/** Reuses #97's adversary, injector, outcomes and barriers. Windows retain ego position
 * and speed; only their local clock/hazard budget restarts. No provider error is retried. */
export async function runJourney(options: JourneyOptions): Promise<void> {
  const { signal, random = Math.random, uuid = () => crypto.randomUUID() } = options;
  const stats = emptySnapshot();
  let origin = randomStop(null, random);
  const publish = () => options.onUpdate({ ...stats, opening: stats.opening ? structuredClone(stats.opening) : null,
    attacks: [...stats.attacks], restartsLog: [...stats.restartsLog] });
  while (!signal.aborted) {
    stats.phase = 'routing'; publish();
    const destination = randomStop(origin, random);
    const route = await options.route(origin, destination, signal);
    signal.throwIfAborted();
    stats.trip++; stats.routeLabel = `${origin.label} → ${destination.label}`;
    const scene = withEventActors(createRouteScenario(route, { ...origin, label: stats.routeLabel }, ''), ADVERSARY_PLAN);
    let state: DriverSimulation = createDriverSimulation(scene);
    const opening = stats.trip === 1 && options.scriptedOpening !== false ? createOpening(state, uuid()) : null;
    if (opening) state = opening.state;
    options.onScene(scene, driverFrame(state));
    if (opening) {
      stats.windows++; stats.trace = opening.trace; stats.opening = opening.result; stats.phase = 'opening';
      stats.driverAction = 'Scripted slow pass'; publish();
      let terminal: Terminal = 'interrupted', message: string | null = null;
      try {
        state = await playOpening(opening, { signal, clock: options.clock,
          onFrame(before, next) {
            state = next; stats.distanceM += next.progress - before.progress; stats.seconds += next.time - before.time;
            options.onFrame(driverFrame(state));
          },
          onPublish() { stats.speedMps = state.speed; publish(); },
        });
        terminal = 'scripted-complete';
      } catch (failure) {
        if (!signal.aborted) { terminal = 'error'; message = failure instanceof Error ? failure.message : 'Opening failed.'; }
        throw failure;
      } finally {
        finishTrace(opening.trace, state, terminal, message); options.onTrace(opening.trace); publish();
      }
      // Carry actual position and speed into live driving, with no scripted pose or policy remaining.
      state = { ...state, scriptedPose: undefined, control: undefined };
      stats.driverAction = 'Waiting for Gemini Decisions';
    }
    const watch = new ProgressWatch({ time: stats.seconds, progress: state.progress });
    let stuck = false, arrived = false;
    while (!signal.aborted && !stuck && !arrived) {
      stats.windows++;
      // Start another bounded #97 run without teleporting the ego or changing its velocity.
      // Unfinished hazards are explicitly horizon-censored in the previous window's trace.
      state = { ...state, time: 0, events: createEvents(ADVERSARY_PLAN, 'event') };
      const trace = createTrace(state, uuid());
      stats.trace = trace;
      let pending: TraceStep | null = null;
      const batch = new AbortController(), cancel = () => batch.abort();
      signal.addEventListener('abort', cancel, { once: true });
      let terminal: Terminal = 'interrupted', message: string | null = null;
      const barrier = adversaryBarrier({ mode: 'carbon', seed: 1, guardsIgnoreHazards: false }, trace, {
        runId: trace.runId, url: options.adversaryUrl, signal: batch.signal, isCurrent: () => !batch.signal.aborted,
        read: () => state,
        write: next => { state = next; recordFrame(trace, state); options.onFrame(driverFrame(state)); },
      })!;
      try {
        const completion = await runContinuousDriver({ initial: state, signal: batch.signal, clock: options.clock,
          readState: () => state,
          barrier: { ...barrier, run(current) {
            const result = barrier.run(current);
            const check = () => {
              if (batch.signal.aborted) return;
              if (trace.adversary?.error) throw new Error(trace.adversary.error);
              stats.phase = 'driving'; publish();
            };
            if (result) { stats.phase = 'thinking'; publish(); return result.then(check); }
            check(); return null;
          } },
          onRequest(step, observation, current) { pending = beginDecision(trace, current, step, observation); },
          requestDecision: (step, observation, requestSignal) => options.driver(trace.runId, step, observation, requestSignal),
          onDecision(response, _observation, current) {
            stats.decisions++; stats.driverAction = response.decision.action;
            if (pending) { pending.response = response; pending.appliedSimulationTime = current.time; pending = null; }
          },
          onFrame(before, next) {
            state = next;
            stats.distanceM += Math.max(0, next.progress - before.progress);
            stats.seconds += Math.max(0, next.time - before.time);
            recordFrame(trace, state); options.onFrame(driverFrame(state));
          },
          onPublish() {
            if (signal.aborted) return;
            stats.speedMps = state.speed;
            stats.attacks = [...stats.attacks.filter(row => row.window !== stats.windows),
              ...attackRecords(trace).map(row => ({ ...row, window: stats.windows }))].slice(-80);
            if (watch.stuck({ time: stats.seconds, progress: state.progress })) { stuck = true; batch.abort(); }
            publish();
          },
        });
        terminal = completion; arrived = completion === 'route-complete';
      } catch (error) {
        if (!stuck && !signal.aborted) { terminal = 'error'; message = error instanceof Error ? error.message : 'Journey failed.'; throw error; }
      } finally {
        batch.abort(); signal.removeEventListener('abort', cancel);
        finishTrace(trace, state, terminal, message);
        stats.attacks = [...stats.attacks.filter(row => row.window !== stats.windows),
          ...attackRecords(trace).map(row => ({ ...row, window: stats.windows }))].slice(-80);
        options.onTrace(trace); publish();
      }
    }
    signal.throwIfAborted();
    if (stuck) {
      stats.restarts++;
      stats.restartsLog = [...stats.restartsLog.slice(-39), { trip: stats.trip, atSeconds: stats.seconds,
        reason: `Less than 2 m progress for ${STUCK_SECONDS} simulated seconds; moving to a random NYC start.` }];
      origin = randomStop(origin, random);
    } else origin = { ...destination, coordinates: route.coordinates.at(-1)! };
  }
}
