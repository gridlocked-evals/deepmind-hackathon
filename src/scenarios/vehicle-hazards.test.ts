import { describe, expect, it } from 'vitest';
import { demoScenario } from '../scene/DemoScenarioProvider';
import { advanceDriver, createDriverSimulation, createPath, driverObservation, prepareDriverDecision, routePosition } from '../agent/physics';
import type { DriverSimulation } from '../agent/physics';
import { advanceEvents, createEvents, preset, sampleEvents, withEventActors } from './events';
import { createTrace, finishTrace, footprintContact, recordFrame, replayScene } from './trace';

const newTypes = ['cut-in-vehicle', 'hard-braking', 'slow-traffic', 'two-car-roadblock'];
function setup(type: string): DriverSimulation {
  const plan = preset(type);
  plan.events[0].trigger = { kind: 'time', seconds: 0 };
  const ego = demoScenario.actors.find(actor => actor.role === 'ego')!;
  // An empty straight route isolates the injected hazard from replayed traffic.
  const scene = { ...demoScenario, id: 'hazard-fixture', actors: [ego],
    geometry: { ...demoScenario.geometry, route: [[0, 0], [500, 0]] as [number, number][] },
    frames: [{ time: 0, signal: 'green' as const, actors: [{ id: ego.id, x: 0, y: 0, heading: 0, speed: 0, braking: false }] }] };
  return prepareDriverDecision({ ...createDriverSimulation(withEventActors(scene, plan)), events: createEvents(plan, 'event') });
}
function sample(state: DriverSimulation, time: number) {
  return sampleEvents(state.events!, time, progress => routePosition(state.path, progress));
}

describe('vehicle hazard motion', () => {
  it('merges a cut-in laterally before continuing forward at a slower speed', () => {
    const state = setup('cut-in-vehicle');
    expect(sample(state, 0)[0]).toMatchObject({ x: 35, y: -4.5, disabled: false });
    expect(driverObservation(state).lead_gap_m).toBeNull();
    expect(sample(state, 1)[0]).toMatchObject({ x: 38, y: -2.25 });
    expect(sample(state, 2)[0]).toMatchObject({ x: 41, y: 0, speed: 3, heading: 0 });
    expect(sample(state, 3)[0].x).toBe(44);
    expect(driverObservation({ ...state, time: 2 }).lead_speed_mps).toBe(3);
  });

  it('hard braking loses speed continuously and remains stopped without becoming disabled', () => {
    const state = setup('hard-braking');
    expect(sample(state, 0)[0]).toMatchObject({ x: 35, speed: 12, braking: true });
    expect(sample(state, 1)[0]).toMatchObject({ x: 44, speed: 6 });
    expect(sample(state, 2)[0]).toMatchObject({ x: 47, speed: 0, disabled: false });
    expect(sample(state, 5)[0]).toEqual(sample(state, 2)[0]);
    expect(driverObservation({ ...state, time: 1 }).lead_speed_mps).toBe(6);
  });

  it('moving traffic follows road bends instead of continuing along the initial tangent', () => {
    const state = setup('slow-traffic');
    state.path = createPath([[0, 0], [40, 0], [40, 200]]);
    expect(sample(state, 1)[0]).toMatchObject({ x: 37.5, y: 0, speed: 2.5, disabled: false });
    expect(sample(state, 4)[0]).toMatchObject({ x: 40, y: 5, speed: 2.5, heading: Math.PI / 2, disabled: false });
  });

  it('a roadblock has two stationary, separate footprints with no scripted collision', () => {
    const state = setup('two-car-roadblock'), actors = sample(state, 0);
    expect(actors).toHaveLength(2);
    expect(sample(state, 5)).toEqual(actors);
    expect(actors.every(actor => actor.speed === 0 && actor.disabled)).toBe(true);
    const definitions = actors.map(actor => state.scenario.actors.find(def => def.id === actor.id)!);
    expect(footprintContact(actors[0], definitions[0], actors[1], definitions[1])).toBe(false);
    expect(driverObservation(state).lead_speed_mps).toBe(0);
    expect(advanceEvents(state.events!, 5, 0, 500).log.some(row => row.kind === 'scripted_collision')).toBe(false);
  });

  it.each(newTypes)('%s remains observable, clears, and replays the recorded motion exactly', type => {
    let state = setup(type);
    const trace = createTrace(state, `fixture-${type}`, 'test-fixture');
    const stop = { action: 'stop' as const, target_speed_mps: 0, reason: 'Fixture waits to observe hazard', confidence: 1 };
    for (let i = 0; i < 100; i++) {
      state = advanceDriver(state, stop, .1); recordFrame(trace, state);
      if (i === 29) {
        expect(driverObservation(state).lead_gap_m).toBeGreaterThan(0);
        expect(driverObservation(state).lead_speed_mps).toBe(type === 'cut-in-vehicle' ? 3 : type === 'slow-traffic' ? 2.5 : 0);
        expect(JSON.stringify(driverObservation(state))).not.toContain(type);
      }
    }
    finishTrace(trace, state, 'interrupted');
    expect(trace.eventLog.filter(row => row.kind === 'cleared')).toHaveLength(1);
    expect(trace.eventLog.some(row => row.kind === 'scripted_collision')).toBe(false);
    expect(trace.frames.at(-1)!.actors.some(actor => actor.id.startsWith('event-'))).toBe(false);
    expect(replayScene(JSON.parse(JSON.stringify(trace))).frames).toEqual(trace.frames);
  });

  it('reserves moving distance at route end independently of hidden cleanup duration', () => {
    for (const durationSeconds of [1, 20]) {
      const plan = preset('cut-in-vehicle');
      Object.assign(plan.events[0], { trigger: { kind: 'time', seconds: 0 }, durationSeconds });
      const rejected = advanceEvents(createEvents(plan, 'event'), 0, 0, 100);
      expect(rejected.applied).toHaveLength(0);
      expect(rejected.log[0].kind).toBe('not_applied_route_end');
      const accepted = advanceEvents(createEvents(plan, 'event'), 0, 0, 120);
      expect(accepted.applied).toHaveLength(1);
      expect(sampleEvents(accepted, 0, progress => ({ x: progress, y: 0, heading: 0 }))[0].x).toBe(35);
    }
  });
});
