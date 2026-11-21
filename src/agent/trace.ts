import type { ScenarioBundle } from '../types';
import type { DriverSimulation } from './physics';
import { MAX_DRIVER_STEPS, SECONDS_PER_DECISION } from './types';
import type { DriverDecision, DriverObservation, DriverResponse, GuardReason } from './types';

export const DRIVER_TRACE_SCHEMA = 'gridlocked-driver-trace/1';

export type TraceTerminal =
  | { state: 'route_complete' | 'decision_limit' | 'stopped_by_user' | 'interrupted' }
  | { state: 'error'; error: string };

export interface GuardEvent {
  reason: Exclude<GuardReason, 'none'>;
  /** Simulation time of the physics tick where this guard started changing applied motion. */
  simulation_time: number;
}

export interface DriverTraceStep {
  step: number;
  request_id: string;
  /** Exactly the observation sent to POST /api/driver/decision; its simulation_time is the hold's start. */
  observation: DriverObservation;
  proposed: DriverDecision;
  applied: {
    start_time: number;
    end_time: number;
    start_speed_mps: number;
    end_speed_mps: number;
    distance_m: number;
  };
  /** `reason` is the first guard to fire in this hold; `events` lists each onset in order. */
  guard: { intervened: boolean; reason: GuardReason; events: GuardEvent[] };
}

export interface DriverTrace {
  schema: typeof DRIVER_TRACE_SCHEMA;
  provenance: {
    source: 'synthetic_simulator_state';
    scenario_id: string;
    scenario_version: string;
    scenario_label: string;
    motion_source: ScenarioBundle['motionSource'] | null;
    route_length_m: number;
  };
  run_id: string;
  model_id: string | null;
  bounds: { max_decisions: number; seconds_per_decision: number };
  steps: DriverTraceStep[];
  /** Null only while the run is still active. */
  terminal: TraceTerminal | null;
}

export interface HoldRecorder {
  start: DriverSimulation;
  events: GuardEvent[];
  last: GuardReason;
  /** Set once the hold's step is in the trace; finalizeHold is a no-op afterwards. */
  finalized: boolean;
}

/** An error whose message is safe to export: the hook's own fixed text or the server's redacted detail.message. */
export class DriverRequestError extends Error {}

/** The error text written to a trace; anything not known to be redacted (e.g. a non-JSON body) is replaced. */
export function traceError(failure: unknown): string {
  return failure instanceof DriverRequestError ? failure.message : 'The driving request failed.';
}

export function createTrace(runId: string, initial: DriverSimulation): DriverTrace {
  const { scenario } = initial;
  return {
    schema: DRIVER_TRACE_SCHEMA,
    provenance: { source: 'synthetic_simulator_state', scenario_id: scenario.id, scenario_version: scenario.version,
      scenario_label: scenario.label, motion_source: scenario.motionSource ?? null, route_length_m: initial.path.length },
    run_id: runId, model_id: null,
    bounds: { max_decisions: MAX_DRIVER_STEPS, seconds_per_decision: SECONDS_PER_DECISION },
    steps: [], terminal: null,
  };
}

export function startHold(start: DriverSimulation): HoldRecorder {
  return { start, events: [], last: 'none', finalized: false };
}

/** Call after every advanceDriver() tick; zero-length ticks are ignored. */
export function recordTick(hold: HoldRecorder, before: DriverSimulation, after: DriverSimulation) {
  if (after.time <= before.time) return;
  if (after.guard !== 'none' && after.guard !== hold.last) hold.events.push({ reason: after.guard, simulation_time: before.time });
  hold.last = after.guard;
}

/** Records a decision and the motion actually applied, once per hold: Stop calls it synchronously before
 * the terminal state, so an immediate export already has the partial step, and the hold's own cleanup
 * then finds it done. A hold cut short by Stop ends at the stop time (zero motion if before its first tick). */
export function finalizeHold(trace: DriverTrace, response: DriverResponse, observation: DriverObservation,
  hold: HoldRecorder, end: DriverSimulation) {
  if (hold.finalized) return;
  hold.finalized = true;
  trace.model_id ??= response.model_id;
  trace.steps.push({
    step: response.step, request_id: response.request_id, observation, proposed: response.decision,
    applied: { start_time: hold.start.time, end_time: end.time, start_speed_mps: hold.start.speed,
      end_speed_mps: end.speed, distance_m: end.progress - hold.start.progress },
    guard: { intervened: hold.events.length > 0, reason: hold.events[0]?.reason ?? 'none', events: hold.events },
  });
}

/** The first terminal state wins, so a late Stop cannot relabel a finished run. */
export function finishTrace(trace: DriverTrace | null, terminal: TraceTerminal) {
  if (trace && !trace.terminal) trace.terminal = terminal;
}

const TERMINAL_STATES = new Set(['route_complete', 'decision_limit', 'stopped_by_user', 'interrupted', 'error']);
const isObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null;

export function readDriverTrace(json: string): DriverTrace {
  const value = JSON.parse(json) as Partial<DriverTrace> | null;
  if (value?.schema !== DRIVER_TRACE_SCHEMA) throw new Error(`Unsupported driver trace schema: ${String(value?.schema)}`);
  if (value.provenance?.source !== 'synthetic_simulator_state') throw new Error('Driver trace is not synthetic simulator state.');
  // Minimal structure, so downstream code can rely on the shape without a full schema validator.
  const steps = value.steps as unknown;
  if (typeof value.run_id !== 'string' || !isObject(value.bounds) || !Array.isArray(steps)
    || !steps.every(step => isObject(step) && isObject(step.applied)
      && typeof step.applied.start_time === 'number' && typeof step.applied.end_time === 'number')
    || !(value.terminal === null || (isObject(value.terminal) && TERMINAL_STATES.has(String(value.terminal.state))))) {
    throw new Error('Driver trace is malformed.');
  }
  return value as DriverTrace;
}
