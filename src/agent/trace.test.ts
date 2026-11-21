import { describe, expect, it } from 'vitest';
import { demoScenario } from '../scene/DemoScenarioProvider';
import type { ScenarioBundle } from '../types';
import { advanceDriver, createDriverSimulation, driverObservation, routeComplete } from './physics';
import type { DriverSimulation } from './physics';
import { addStep, createTrace, DRIVER_TRACE_SCHEMA, finishTrace, readDriverTrace, recordTick, startHold } from './trace';
import type { DriverTrace } from './trace';
import { MAX_DRIVER_STEPS, SECONDS_PER_DECISION, SPEED_LIMIT_MPS } from './types';
import type { DecisionEntry, DriverDecision, DriverResponse } from './types';

const accelerate: DriverDecision = { action: 'accelerate', target_speed_mps: SPEED_LIMIT_MPS, reason: 'Proceed along the route.', confidence: 0.9 };
const cruise: DriverDecision = { action: 'cruise', target_speed_mps: 4, reason: 'Hold speed.', confidence: 0.8 };

function streetScene(points: [number, number][] = [[0, 0], [1000, 0]]): ScenarioBundle {
  return { ...demoScenario, id: 'test-route', label: 'Test route', geometryKind: 'street-route', motionSource: 'driving-route',
    geometry: { roads: [], sidewalks: [], crosswalks: [], lanes: [], route: points },
    actors: [{ ...demoScenario.actors.find(actor => actor.role === 'ego')!, routeId: 'test-route' }],
    frames: [{ time: 0, signal: 'green', actors: [{ id: 'ego', x: 0, y: 0, heading: 0, speed: 0, braking: false }] }],
  };
}

interface FixtureOptions {
  /** Stop the run at this step index after this many 0.1 s ticks of its hold (the visibility interrupt). */
  interruptAt?: { step: number; ticks: number };
  /** The request for this step fails instead of returning a decision. */
  failAt?: { step: number; message: string };
}

/** Mirrors useGeminiDriver's loop with fixed 0.1 s ticks and canned responses; no network. */
function runFixture(initial: DriverSimulation, decisions: DriverDecision[], options: FixtureOptions = {}) {
  const runId = 'fixture-run-0001';
  const trace = createTrace(runId, initial);
  const log: DecisionEntry[] = [];
  let simulation = initial;
  for (let step = 0; step < MAX_DRIVER_STEPS; step++) {
    const observation = driverObservation(simulation);
    if (options.failAt?.step === step) { finishTrace(trace, { state: 'error', error: options.failAt.message }); return { trace, log }; }
    const response: DriverResponse = { run_id: runId, step, model_id: 'gemini-test', request_id: `req-${step}`,
      decision: decisions[Math.min(step, decisions.length - 1)], max_steps: MAX_DRIVER_STEPS };
    log.push({ ...response, simulationTime: observation.simulation_time });
    const hold = startHold(simulation);
    const until = simulation.time + SECONDS_PER_DECISION;
    for (let tick = 0; simulation.time < until - 0.00001 && !routeComplete(simulation); tick++) {
      if (options.interruptAt?.step === step && tick === options.interruptAt.ticks) {
        finishTrace(trace, { state: 'interrupted' });
        addStep(trace, response, observation, hold, simulation);
        return { trace, log };
      }
      const before = simulation;
      simulation = advanceDriver(before, response.decision, Math.min(0.1, until - before.time));
      recordTick(hold, before, simulation);
    }
    addStep(trace, response, observation, hold, simulation);
    if (routeComplete(simulation)) { finishTrace(trace, { state: 'route_complete' }); return { trace, log }; }
  }
  finishTrace(trace, { state: 'decision_limit' });
  return { trace, log };
}

function keys(value: unknown): string[] {
  if (!value || typeof value !== 'object') return [];
  return Object.entries(value).flatMap(([key, child]) => [key, ...keys(child)]);
}

describe('driver trace export', () => {
  it('exports a short successful run with ordered steps matching the decision log', () => {
    const { trace, log } = runFixture(createDriverSimulation(streetScene([[0, 0], [12, 0]])), [accelerate, accelerate, cruise]);
    expect(trace.terminal).toEqual({ state: 'route_complete' });
    expect(trace.run_id).toBe('fixture-run-0001');
    expect(trace.model_id).toBe('gemini-test');
    expect(trace.steps.length).toBeGreaterThan(1);
    expect(trace.steps.length).toBeLessThan(MAX_DRIVER_STEPS);
    expect(trace.steps.map(step => [step.step, step.request_id, step.proposed, step.observation.simulation_time]))
      .toEqual(log.map(entry => [entry.step, entry.request_id, entry.decision, entry.simulationTime]));
    trace.steps.forEach((step, i) => {
      expect(step.applied.start_time).toBe(step.observation.simulation_time);
      expect(step.applied.start_speed_mps).toBe(step.observation.speed_mps);
      expect(step.applied.end_time).toBeGreaterThan(step.applied.start_time);
      expect(step.applied.end_time - step.applied.start_time).toBeLessThanOrEqual(SECONDS_PER_DECISION + 1e-9);
      if (i > 0) expect(step.applied.start_time).toBe(trace.steps[i - 1].applied.end_time);
    });
    const travelled = trace.steps.reduce((sum, step) => sum + step.applied.distance_m, 0);
    expect(travelled).toBeCloseTo(trace.provenance.route_length_m, 1);
  });

  it('distinguishes a decision-limited run from route completion', () => {
    const { trace } = runFixture(createDriverSimulation(streetScene()), [accelerate]);
    expect(trace.terminal).toEqual({ state: 'decision_limit' });
    expect(trace.steps).toHaveLength(MAX_DRIVER_STEPS);
    expect(trace.steps.at(-1)!.applied.end_time).toBeCloseTo(MAX_DRIVER_STEPS * SECONDS_PER_DECISION, 6);
  });

  it('ends an interrupted run at the interruption without inventing later observations or actions', () => {
    const { trace } = runFixture(createDriverSimulation(streetScene()), [accelerate], { interruptAt: { step: 1, ticks: 15 } });
    expect(trace.terminal).toEqual({ state: 'interrupted' });
    expect(trace.steps.map(step => step.step)).toEqual([0, 1]);
    expect(trace.steps[1].applied.end_time).toBeCloseTo(4.5, 6);
    finishTrace(trace, { state: 'stopped_by_user' });
    expect(trace.terminal).toEqual({ state: 'interrupted' });
  });

  it('exports a failed request as an error with its message and no step for it', () => {
    const { trace } = runFixture(createDriverSimulation(streetScene()), [accelerate], { failAt: { step: 2, message: 'The Gemini request timed out.' } });
    expect(trace.terminal).toEqual({ state: 'error', error: 'The Gemini request timed out.' });
    expect(trace.steps).toHaveLength(2);
  });

  it('separates a proposed accelerate from motion applied under the leader guard', () => {
    const scenario = streetScene([[0, 0], [100, 0]]);
    scenario.actors.push({ ...scenario.actors[0], id: 'leader', role: 'traffic', length: 5 });
    scenario.frames[0].actors.push({ id: 'leader', x: 20, y: 0, heading: 0, speed: 0, braking: true });
    const { trace } = runFixture(createDriverSimulation(scenario), [accelerate], { interruptAt: { step: 3, ticks: 0 } });
    expect(trace.steps.every(step => step.proposed.action === 'accelerate')).toBe(true);
    const first = trace.steps.find(step => step.guard.intervened)!;
    expect(first.guard.reason).toBe('leader_guard');
    expect(first.guard.events[0]).toEqual({ reason: 'leader_guard', simulation_time: expect.any(Number) });
    expect(first.guard.events[0].simulation_time).toBeGreaterThanOrEqual(first.applied.start_time);
    expect(first.guard.events[0].simulation_time).toBeLessThan(first.applied.end_time);
    const last = trace.steps.at(-1)!;
    expect(last.applied.end_speed_mps).toBeLessThan(0.1);
    expect(last.applied.end_speed_mps).toBeLessThan(last.proposed.target_speed_mps);
  });

  it('reports the red-line guard when it overrides an accelerate at a red signal', () => {
    const scenario = { ...demoScenario, actors: demoScenario.actors.filter(actor => actor.role === 'ego'),
      frames: demoScenario.frames.map(frame => ({ ...frame, actors: frame.actors.filter(actor => actor.id === 'ego') })) };
    const base = createDriverSimulation(scenario);
    const { trace } = runFixture({ ...base, time: 15, progress: base.stopProgress! - 20, speed: 7 }, [accelerate],
      { interruptAt: { step: 1, ticks: 0 } });
    expect(trace.steps[0].observation.signal).toBe('red');
    expect(trace.steps[0].guard).toMatchObject({ intervened: true, reason: 'red_line_guard' });
    // At 20 m and 7 m/s the red-line cap only binds once it falls below the proposed target, after the hold starts.
    expect(trace.steps[0].guard.events[0].simulation_time).toBeGreaterThan(15);
    expect(trace.steps[0].guard.events[0].simulation_time).toBeLessThan(18);
    expect(trace.steps[0].applied.end_speed_mps).toBeLessThan(trace.steps[0].applied.start_speed_mps);
  });

  it('attributes braking to the guard that changes acceleration when a later cap is redundant', () => {
    const scenario = { ...streetScene(), id: demoScenario.id };
    const ego = scenario.actors[0];
    scenario.actors.push({ ...ego, id: 'leader', role: 'traffic' });
    scenario.frames[0].actors.push({ id: 'leader', x: ego.length + 10.2, y: 0, heading: 0, speed: 0, braking: true });
    const initial = { ...createDriverSimulation(scenario), time: 15, progress: 0, speed: 7, stopProgress: 1 };
    const withoutRedLine = advanceDriver({ ...initial, stopProgress: null }, accelerate, 0.1);
    const withBothGuards = advanceDriver(initial, accelerate, 0.1);
    expect(driverObservation(initial).signal).toBe('red');
    expect(driverObservation(initial).lead_gap_m).toBeCloseTo(10.2);
    expect(withBothGuards.speed).toBe(withoutRedLine.speed);
    expect(withBothGuards.progress).toBe(withoutRedLine.progress);
    expect(withoutRedLine.guard).toBe('leader_guard');
    expect(withBothGuards.guard).toBe('leader_guard');
  });

  it('reports no intervention on an open road', () => {
    const { trace } = runFixture(createDriverSimulation(streetScene()), [accelerate], { interruptAt: { step: 2, ticks: 0 } });
    expect(trace.steps[0].guard).toEqual({ intervened: false, reason: 'none', events: [] });
  });

  it('labels the trace as versioned synthetic simulator state and rejects unknown schema versions', () => {
    const { trace } = runFixture(createDriverSimulation(streetScene()), [accelerate], { interruptAt: { step: 0, ticks: 5 } });
    expect(trace.schema).toBe(DRIVER_TRACE_SCHEMA);
    expect(trace.provenance).toMatchObject({ source: 'synthetic_simulator_state', scenario_id: 'test-route', motion_source: 'driving-route' });
    expect(trace.bounds).toEqual({ max_decisions: 12, seconds_per_decision: 3 });
    const json = JSON.stringify(trace);
    expect(readDriverTrace(json)).toEqual(trace);
    expect(() => readDriverTrace(JSON.stringify({ ...trace, schema: 'gridlocked-driver-trace/2' }))).toThrow(/Unsupported driver trace schema/);
    expect(() => readDriverTrace(JSON.stringify({ ...trace, schema: undefined }))).toThrow(/Unsupported driver trace schema/);
    expect(() => readDriverTrace(JSON.stringify({ ...trace, provenance: { ...trace.provenance, source: 'camera' } }))).toThrow(/synthetic/);
  });

  it('contains no credential or token fields', () => {
    const traces: DriverTrace[] = [
      runFixture(createDriverSimulation(streetScene([[0, 0], [12, 0]])), [accelerate]).trace,
      runFixture(createDriverSimulation(streetScene()), [accelerate], { failAt: { step: 1, message: 'Gateway unavailable.' } }).trace,
    ];
    for (const trace of traces) {
      expect(keys(trace).filter(key => /key|token|secret|credential|password|auth|cookie|header/i.test(key))).toEqual([]);
    }
  });
});
