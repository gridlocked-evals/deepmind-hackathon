import { describe, expect, it } from 'vitest';
import { demoScenario } from '../scene/DemoScenarioProvider';
import { advanceDriver, createDriverSimulation, driverFrame, driverObservation, observedEventIds, prepareDriverDecision, validateEventRun } from '../agent/physics';
import type { DriverSimulation } from '../agent/physics';
import type { DriverDecision, DriverResponse } from '../agent/types';
import { createTrace as createActionTrace, finalizeHold, finishTrace as finishActionTrace, readDriverTrace, recordTick, startHold } from '../agent/trace';
import { advanceEvents, createEvents, parseScenario, preset, registerEvent, requestEvent, sampleEvents, withEventActors } from './events';
import { beginDecision, compareTraces, createTrace, finishTrace, footprintContact, recordFrame, replayScene, summarizeTrace } from './trace';

const accelerate: DriverDecision = { action: 'accelerate', target_speed_mps: 10, reason: 'Fixture action', confidence: .8 };
function setup(type = 'car-crash', mode: 'event' | 'baseline' = 'event') {
  const plan = preset(type);
  return { ...createDriverSimulation(withEventActors(demoScenario, plan)), events: createEvents(plan, mode) } as DriverSimulation;
}
function advance(state: DriverSimulation, seconds: number, decision = accelerate) {
  for (let i = 0; i < Math.round(seconds * 10); i++) state = advanceDriver(state, decision, .1);
  return state;
}
function run(mode: 'event' | 'baseline') {
  let state = setup('car-crash', mode);
  const trace = createTrace(state, `fixture-${mode}`, 'test-fixture');
  for (let step = 0; step < 12; step++) {
    state = prepareDriverDecision(state); recordFrame(trace, state);
    const row = beginDecision(trace, state, step);
    const decision: DriverDecision = row.observation.lead_gap_m !== null && row.observation.lead_gap_m < 35
      ? { action: 'stop', target_speed_mps: 0, reason: 'Fixture sees an obstacle', confidence: .9 } : accelerate;
    row.appliedSimulationTime = state.time;
    row.response = { run_id: trace.runId, step, decision, model_id: 'fixture', request_id: `fixture-${step}`, max_steps: 12 };
    for (let tick = 0; tick < 30; tick++) { state = advanceDriver(state, decision, .1); recordFrame(trace, state); }
  }
  finishTrace(trace, state, 'decision-limit');
  return trace;
}

describe('scenario injection boundary', () => {
  it('validates versions, capabilities, bounds, unique IDs and extra fields before a run', () => {
    const plan = preset();
    for (const patch of [{ runtime: 'genie' }, { schemaVersion: 'v2' }, { future: true }, { events: [] },
      { events: [plan.events[0], plan.events[0]] }, { events: [{ ...plan.events[0], type: 'unknown' }] },
      { events: [{ ...plan.events[0], trigger: { kind: 'manual', seconds: 0 } }] },
      { events: [{ ...plan.events[0], parameters: { distanceAheadM: NaN, approachSeconds: 2 } }] },
      { events: [{ ...plan.events[0], cleanup: 'persist' }] }]) expect(() => parseScenario({ ...plan, ...patch })).toThrow();
    expect(parseScenario(JSON.parse(JSON.stringify(plan)))).toEqual(plan);
  });
  it('visibly moves two cars into contact, disables them, then removes the hazard', () => {
    let state = prepareDriverDecision(advance(setup(), 3));
    const initial = driverFrame(state).actors.filter(actor => actor.id.startsWith('event-'));
    expect(initial).toHaveLength(2); expect(initial.every(actor => !actor.disabled)).toBe(true);
    state = advance(state, 2);
    const impacted = driverFrame(state).actors.filter(actor => actor.id.startsWith('event-'));
    expect(impacted.every(actor => actor.disabled && actor.speed === 0)).toBe(true);
    expect(impacted[1].x).not.toBe(initial[1].x);
    const defs = impacted.map(actor => state.scenario.actors.find(def => def.id === actor.id)!);
    expect(footprintContact(impacted[0], defs[0], impacted[1], defs[1])).toBe(true);
    expect(state.events!.log.filter(row => row.kind === 'scripted_collision')).toHaveLength(1);
    state = advance(state, 8);
    expect(driverFrame(state).actors.filter(actor => actor.id.startsWith('event-'))).toHaveLength(0);
    expect(state.events!.log.filter(row => row.kind === 'cleared')).toHaveLength(1);
  });
  it('makes the current obstacle observable without leaking event plans, triggers or expected answers', () => {
    const state = prepareDriverDecision(advance(setup(), 3));
    expect(observedEventIds(state)).toEqual(['hazard-1']);
    const observation = driverObservation(state);
    expect(observation.lead_gap_m).toBeLessThan(35);
    const text = JSON.stringify(observation);
    for (const field of ['expectedOutcomes', 'car-crash', 'hazard-1', 'trigger', 'durationSeconds']) expect(text).not.toContain(field);
    expect(observedEventIds(setup())).toEqual([]);
  });
  it('includes a sideways crashed car in obstacle geometry', () => {
    let state = advance(setup(), 5);
    // Remove the same-direction car: the perpendicular car must still be sensed.
    const first = state.scenario.actors.find(actor => actor.id === 'event-hazard-1-0')!;
    state = { ...state, scenario: { ...state.scenario, actors: state.scenario.actors.filter(actor => actor !== first) } };
    expect(observedEventIds(state)).toEqual(['hazard-1']);
    expect(driverObservation(state).lead_speed_mps).toBe(0);
  });
  it('attributes an observed actor only to its exact event when IDs share a prefix', () => {
    const plan = preset();
    const event = plan.events[0];
    plan.events = [{ ...event, id: 'hazard', parameters: { ...event.parameters, distanceAheadM: 80 } },
      { ...event, id: 'hazard-near' }];
    let state = { ...createDriverSimulation(withEventActors(demoScenario, plan)), events: createEvents(plan, 'event') };
    state = advance(state, 5) as typeof state;
    expect(observedEventIds(state)).toEqual(['hazard-near']);
  });
  it('queues manual injection across in-flight decisions and applies it once at a boundary', () => {
    let state = setup();
    const plan = preset(); plan.events[0].trigger = { kind: 'manual' };
    state.events = createEvents(plan, 'event');
    const before = driverObservation(state);
    state.events = requestEvent(state.events, 'hazard-1', 0);
    expect(requestEvent(state.events, 'hazard-1', 0)).toBe(state.events);
    expect(driverObservation(state)).toEqual(before);
    state = advance(state, 3);
    expect(state.events!.applied).toHaveLength(0);
    state = prepareDriverDecision(state);
    expect(state.events!.applied[0].appliedAt).toBe(3);
    state = prepareDriverDecision(advance(state, 2));
    expect(state.events!.applied).toHaveLength(1);
    expect(state.events!.log.filter(row => row.kind === 'applied')).toHaveLength(1);
  });
  it('supports frame triggers and deterministic resets with no stale event state', () => {
    const plan = preset(); plan.events[0].trigger = { kind: 'frame', frame: 7 };
    let state = createEvents(plan, 'event');
    state = advanceEvents(state, .6, 0, 200); expect(state.applied).toHaveLength(0);
    state = advanceEvents(state, .7, 0, 200); expect(state.applied[0].appliedFrame).toBe(7);
    expect(createEvents(plan, 'event').applied).toHaveLength(0);
    expect(advance(setup(), 10)).toEqual(advance(setup(), 10));
    expect(advance(setup('car-crash','baseline'), 10).events!.applied).toHaveLength(0);
  });
  it('adds another event type through the registry without touching the decision loop', () => {
    registerEvent('test-custom', { label: 'Test event', actorCount: 1, contact: false, sample: (_age, event, pose) => [
      { id: `event-${event.spec.id}-0`, ...pose, speed: 0, braking: true },
    ] });
    const plan = preset('test-custom');
    const state = advanceEvents(createEvents(plan, 'event'), 3, 0, 200);
    expect(sampleEvents(state, 3, () => ({ x: 0, y: 0, heading: 0 }))).toHaveLength(1);
    expect(advance(setup('stalled-vehicle'), 5).events!.log.some(row => row.kind === 'disabled')).toBe(true);
    expect(advance(setup('stalled-vehicle'), 5).events!.log.some(row => row.kind === 'scripted_collision')).toBe(false);
  });
  it('rejects routes too short to place the hazard and records exhausted-route triggers once', () => {
    const state = setup();
    state.path = { ...state.path, length: state.progress + 20 };
    expect(() => validateEventRun(state)).toThrow(/too short/);
    let events = advanceEvents(createEvents(preset(), 'event'), 3, 95, 100);
    events = advanceEvents(events, 4, 96, 100);
    expect(events.applied).toHaveLength(0);
    expect(events.log.filter(row => row.kind === 'not_applied_route_end')).toHaveLength(1);
  });
});

describe('scenario trace and replay', () => {
  it('measures reaction when a response is applied after its observation, including inference motion', () => {
    let state = setup();
    const trace = createTrace(state, 'fixture-overlapped-inference', 'test-fixture');
    state = prepareDriverDecision(advance(state, 3)); recordFrame(trace, state);
    const entry = beginDecision(trace, state, 0);
    state = advance(state, 1.2); recordFrame(trace, state);
    entry.response = { run_id: trace.runId, step: 0, model_id: 'gemini-decision-1', request_id: 'fixture-response', max_steps: 60,
      decision: { action: 'stop', target_speed_mps: 0, confidence: .9, reason: 'Fixture observes obstruction.' } };
    expect(summarizeTrace(trace).events[0].modelReactionTime).toBeNull(); // received but not applied
    entry.appliedSimulationTime = state.time;
    entry.wallLatencyMs = 1200;
    const reaction = summarizeTrace(trace).events[0];
    expect(reaction.firstModelVisibleTime).toBe(3);
    expect(reaction.modelReactionTime).toBe(4.2);
    expect(reaction.responseLatencySimulationSeconds).toBeCloseTo(1.2);
    expect(reaction.responseLatencyFrames).toBe(12);
    expect(entry.observation.simulation_time).toBe(3);
  });

  it('replays a full continuous run and retains compatibility with historical scenario traces', () => {
    let state = setup('car-crash', 'baseline');
    const trace = createTrace(state, 'fixture-sixty-seconds', 'test-fixture');
    for (let tick = 0; tick < 600; tick++) { state = advanceDriver(state, accelerate, .1); recordFrame(trace, state); }
    finishTrace(trace, state, 'time-limit');
    expect(trace.schemaVersion).toBe('gridlocked-driver-trace.v2');
    expect(trace.modelPolicy).toEqual({ promptVersion: 'sim-driver-decisions-v2', service: 'decisions', modelId: 'gemini-decision-1' });
    expect(trace.frames).toHaveLength(601);
    expect(replayScene(trace).duration).toBe(60);
    expect(replayScene({ ...run('event'), schemaVersion: 'gridlocked-driver-trace.v1' }).duration).toBe(36);
    expect(() => replayScene({ ...trace, frames: [...trace.frames, { ...trace.frames.at(-1), time: 60.1 }] })).toThrow();
  });

  it('preserves the action trace and effective guards during an interrupted crash run', () => {
    let state = setup();
    const scenarioTrace = createTrace(state, 'fixture-both-traces', 'test-fixture');
    const actionTrace = createActionTrace(scenarioTrace.runId, state);
    for (let step = 0; step < 4; step++) {
      state = prepareDriverDecision(state); recordFrame(scenarioTrace, state);
      const entry = beginDecision(scenarioTrace, state, step);
      const response: DriverResponse = { run_id: scenarioTrace.runId, step, decision: accelerate,
        model_id: 'fixture', request_id: `fixture-${step}`, max_steps: 12 };
      entry.response = response; entry.appliedSimulationTime = state.time;
      const hold = startHold(state);
      for (let tick = 0; tick < (step === 3 ? 5 : 30); tick++) {
        const before = state; state = advanceDriver(state, accelerate, .1);
        recordTick(hold, before, state); recordFrame(scenarioTrace, state);
      }
      finalizeHold(actionTrace, response, entry.observation, hold, state);
      finalizeHold(actionTrace, response, entry.observation, hold, state);
    }
    finishActionTrace(actionTrace, { state: 'stopped_by_user' });
    finishTrace(scenarioTrace, state, 'interrupted');
    const exported = readDriverTrace(JSON.stringify(actionTrace));
    expect(exported.steps).toHaveLength(4);
    expect(exported.steps.map(step => step.observation)).toEqual(scenarioTrace.steps.map(step => step.observation));
    expect(exported.steps.some(step => step.guard.events.some(event => event.reason === 'leader_guard'))).toBe(true);
    expect(exported.steps.at(-1)!.applied.end_time).toBe(scenarioTrace.frames.at(-1)!.time);
    expect(exported.steps.at(-1)!.applied.end_time).toBe(9.5);
    expect(replayScene(scenarioTrace).frames).toEqual(scenarioTrace.frames);
  });
  it('separates proposed action, intervention, scripted contact and ego contact', () => {
    let state = setup();
    const trace = createTrace(state, 'fixture-override', 'test-fixture');
    for (let tick = 0; tick < 130; tick++) { state = advanceDriver(state, accelerate, .1); recordFrame(trace, state); }
    finishTrace(trace, state, 'interrupted');
    const summary = summarizeTrace(trace);
    expect(summary.scriptedCrashCount).toBe(1);
    expect(summary.controllerInterventionFrames).toBeGreaterThan(0);
    expect(summary.egoContactFrames).toBe(0);
    expect(summary.events[0].modelReactionTime).toBeNull();
    expect(trace.telemetry.some(row => row.control?.requestedTargetMps !== row.control?.constrainedTargetMps)).toBe(true);
    expect(trace.terminal.reason).toBe('interrupted');
  });
  it('exports ordered exact observations, decisions, applied frames and replayable outcomes', () => {
    const trace = run('event');
    expect(trace.steps).toHaveLength(12); expect(trace.frames).toHaveLength(361);
    expect(trace.terminal).toMatchObject({ reason: 'decision-limit', simulationTime: 36 });
    expect(trace.steps.every(row => (row.response as DriverResponse).step === row.step)).toBe(true);
    const replay = replayScene(JSON.parse(JSON.stringify(trace)));
    expect(replay.frames).toEqual(trace.frames); expect(replay.motionSource).toBe('event-replay');
    expect(replay.frames.at(-1)!.time).toBe(36);
    expect(summarizeTrace(trace).events[0].firstModelVisibleTime).toBe(3);
    expect(summarizeTrace(trace).events[0].modelReactionTime).toBe(3);
    expect(compareTraces(run('baseline'), trace)).not.toBeNull();
    const mismatch = structuredClone(trace); mismatch.scene.seed++;
    expect(compareTraces(run('baseline'), mismatch)).toBeNull();
  });
  it('retains a pending observation and truthful terminal reason after interruption or failure', () => {
    const state = setup(), trace = createTrace(state, 'fixture-failure', 'test-fixture');
    beginDecision(trace, state, 0);
    finishTrace(trace, state, 'error', 'Fixture gateway failure');
    expect(trace.steps[0].response).toBeNull(); expect(trace.frames).toHaveLength(1);
    expect(trace.terminal.reason).toBe('error'); expect(trace.terminal.simulationTime).toBe(0);
    expect(replayScene(trace).duration).toBe(0);
  });
  it('compares time-zero events against the pre-injection initial state', () => {
    const make = (mode: 'event' | 'baseline') => {
      let state = setup('car-crash', mode);
      state.events!.plan.events[0].trigger = { kind: 'time', seconds: 0 };
      const trace = createTrace(state, `fixture-zero-${mode}`, 'test-fixture');
      state = prepareDriverDecision(state); recordFrame(trace, state); finishTrace(trace, state, 'interrupted');
      return trace;
    };
    const baseline = make('baseline'), event = make('event');
    expect(baseline.frames[0]).not.toEqual(event.frames[0]);
    expect(compareTraces(baseline, event)).not.toBeNull();
  });
  it('rejects malformed and unsupported replay files', () => {
    const trace = run('event');
    expect(() => replayScene({ ...trace, schemaVersion: 'future' })).toThrow();
    expect(() => replayScene({ ...trace, frames: [{ ...trace.frames[0], time: NaN }] })).toThrow();
    expect(() => replayScene({ ...trace, frames: [{ ...trace.frames[0], actors: [{ ...trace.frames[0].actors[0], id: 'unknown' }] }] })).toThrow();
  });
});
