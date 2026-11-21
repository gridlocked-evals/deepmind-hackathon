import { describe, expect, it } from 'vitest';
import { demoScenario, sampleScenario } from '../scene/DemoScenarioProvider';
import type { ScenarioBundle } from '../types';
import { advanceDriver, COMFORTABLE_BRAKING, createDriverSimulation, driverEligibility, driverFrame, driverObservation, MAX_ACCELERATION, routeComplete, routePosition } from './physics';
import { SPEED_LIMIT_MPS } from './types';
import type { DriverDecision } from './types';
import { footprintContact } from '../scenarios/trace';
import { createEvents, preset, withEventActors } from '../scenarios/events';

const accelerate: DriverDecision = { action: 'accelerate', target_speed_mps: SPEED_LIMIT_MPS, reason: 'Proceed along the route.', confidence: 0.9 };
const stop: DriverDecision = { action: 'stop', target_speed_mps: 0, reason: 'Wait here.', confidence: 0.9 };
const goAround: DriverDecision = { action: 'go_around', target_speed_mps: 4, reason: 'Use the clear adjacent lane.', confidence: .9 };

function streetScene(points: [number, number][] = [[0, 0], [100, 0]]): ScenarioBundle {
  return { ...demoScenario, id: 'test-route', label: 'Test route', geometryKind: 'street-route', motionSource: 'driving-route',
    geometry: { roads: [], sidewalks: [], crosswalks: [], lanes: [], route: points },
    actors: [{ ...demoScenario.actors.find(actor => actor.role === 'ego')!, routeId: 'test-route' }],
    frames: [{ time: 0, signal: 'green', actors: [{ id: 'ego', x: 0, y: 0, heading: 0, speed: 0, braking: false }] }],
  };
}

function bypassScene(): ScenarioBundle {
  const scene = streetScene([[0, 0], [200, 0]]);
  scene.geometry.roads = [[[-20, -2], [220, -2], [220, 6], [-20, 6]]];
  scene.geometry.lanes = [
    { id: 'test-route', points: [[-20, 0], [220, 0]], width: 4, direction: 'east' },
    { id: 'adjacent', points: [[-20, 4], [220, 4]], width: 4, direction: 'east' },
  ];
  scene.frames[0].actors[0].x = 20;
  scene.actors.push({ ...scene.actors[0], id: 'obstacle', role: 'traffic', length: 5 });
  scene.frames[0].actors.push({ id: 'obstacle', x: 50, y: 0, heading: 0, speed: 0, braking: true, disabled: true });
  return scene;
}

describe('bounded adjacent-lane bypass', () => {
  it('moves laterally, passes a stopped obstacle without contact, and rejoins the original route', () => {
    const scene = bypassScene();
    let state = createDriverSimulation(scene), maxOffset = 0;
    expect(driverObservation(state)).toMatchObject({ go_around_available: true, go_around_side: 'left', go_around_active: false });
    let planStart: number | undefined;
    for (let tick = 0; tick < 300; tick++) {
      state = advanceDriver(state, goAround, .1);
      const ego = driverFrame(state).actors.find(actor => actor.id === 'ego')!;
      maxOffset = Math.max(maxOffset, ego.y);
      if (state.bypass) { planStart ??= state.bypass.start; expect(state.bypass.start).toBe(planStart); }
      expect(state.speed).toBeLessThanOrEqual(4 + 1e-9);
      expect(footprintContact(ego, state.ego, scene.frames[0].actors[1], scene.actors[1])).toBe(false);
      if (state.progress > 75) break;
    }
    expect(maxOffset).toBeGreaterThan(3.9);
    expect(state.progress).toBeGreaterThan(70);
    expect(state.bypass).toBeUndefined();
    const rejoined = driverFrame(state).actors[0];
    expect(rejoined.y).toBeCloseTo(0, 8);
    expect(rejoined.heading).toBeCloseTo(0, 8);
    const continued = advanceDriver(state, goAround, .1);
    expect(continued.speed).toBeGreaterThan(3.9);
    expect(continued.progress).toBeGreaterThan(state.progress);
    expect(continued.bypass).toBeUndefined();
  });

  it('rejects unknown, narrow, oncoming, occupied and predicted rear-approach corridors', () => {
    const variants = [
      (scene: ScenarioBundle) => { scene.geometry.lanes = []; },
      (scene: ScenarioBundle) => { scene.geometry.lanes[1].width = 2; },
      (scene: ScenarioBundle) => { scene.geometry.lanes[1].direction = 'west'; scene.geometry.lanes[1].points.reverse(); },
      (scene: ScenarioBundle) => { scene.geometry.roads = []; },
      (scene: ScenarioBundle) => { scene.geometry.roads = [[[-20, -2], [220, -2], [220, 2], [-20, 2]]]; },
      (scene: ScenarioBundle) => {
        scene.actors.push({ ...scene.actors[1], id: 'adjacent-car', routeId: 'adjacent' });
        scene.frames[0].actors.push({ id: 'adjacent-car', x: 40, y: 4, heading: 0, speed: 0, braking: false });
      },
      (scene: ScenarioBundle) => {
        scene.actors.push({ ...scene.actors[1], id: 'rear-car', routeId: 'adjacent' });
        scene.frames[0].actors.push({ id: 'rear-car', x: -10, y: 4, heading: 0, speed: 10, braking: false });
      },
      (scene: ScenarioBundle) => {
        scene.actors.push({ ...scene.actors[1], id: 'oncoming-car', routeId: 'adjacent' });
        scene.frames[0].actors.push({ id: 'oncoming-car', x: 95, y: 4, heading: Math.PI, speed: 8, braking: false });
      },
    ];
    for (const change of variants) {
      const scene = bypassScene(); change(scene);
      const initial = createDriverSimulation(scene);
      expect(driverObservation(initial).go_around_available).toBe(false);
      const state = advanceDriver(initial, goAround, .1);
      expect(state.bypass).toBeUndefined();
      expect(driverFrame(state).actors[0].y).toBe(0);
      expect(state.guard).toBe('maneuver_guard');
    }
  });

  it('freezes an active maneuver if an unrelated actor occupies its corridor', () => {
    let state = createDriverSimulation(bypassScene());
    for (let tick = 0; tick < 40; tick++) state = advanceDriver(state, goAround, .1);
    expect(state.bypass).toBeDefined();
    const before = driverFrame(state).actors[0];
    const scene = structuredClone(state.scenario);
    scene.actors.push({ ...scene.actors[1], id: 'new-obstacle', routeId: 'unrelated-lane' });
    scene.frames[0].actors.push({ id: 'new-obstacle', x: state.progress + 12, y: 4, heading: Math.PI / 2, speed: 0, braking: true });
    state = { ...state, scenario: scene };
    expect(driverObservation(state)).toMatchObject({ go_around_available: false, go_around_active: true, go_around_side: 'left' });
    state = advanceDriver(state, goAround, .1);
    expect(state.speed).toBe(0); expect(state.guard).toBe('maneuver_guard'); expect(state.braking).toBe(true);
    expect(driverFrame(state).actors[0]).toMatchObject({ x: before.x, y: before.y, heading: before.heading });
  });

  it('holds its lateral position when stopped, then rejoins after the original obstacle disappears', () => {
    let state = createDriverSimulation(bypassScene());
    for (let tick = 0; tick < 40; tick++) state = advanceDriver(state, goAround, .1);
    for (let tick = 0; tick < 100; tick++) state = advanceDriver(state, stop, .1);
    const held = driverFrame(state).actors[0];
    state = advanceDriver(state, stop, .1);
    expect(driverFrame(state).actors[0].y).toBeCloseTo(held.y, 6);
    expect(state.bypass).toBeDefined();
    state = { ...state, scenario: { ...state.scenario, frames: state.scenario.frames.map(frame => ({ ...frame, actors: frame.actors.filter(actor => actor.id !== 'obstacle') })) } };
    for (let tick = 0; tick < 300 && state.bypass; tick++) state = advanceDriver(state, goAround, .1);
    expect(state.bypass).toBeUndefined(); expect(driverFrame(state).actors[0].y).toBeCloseTo(0, 8);
  });

  it('preserves the stop-line guard while traversing an adjacent lane', () => {
    let state = createDriverSimulation(bypassScene());
    state = advanceDriver(state, goAround, .1);
    state = { ...state, scenario: { ...state.scenario, id: demoScenario.id }, time: 15, stopProgress: 44, signalStop: true };
    for (let tick = 0; tick < 130; tick++) { state = advanceDriver(state, goAround, .1); expect(state.progress).toBeLessThanOrEqual(44); }
    expect(state.speed).toBeLessThan(.1);
    expect(driverObservation(state).go_around_available).toBe(false);
    expect(state.bypass).toBeDefined();
  });

  it('offers a corridor around the full two-car crash and clears both footprints before rejoining', () => {
    const plan = preset(); plan.events[0].trigger = { kind: 'time', seconds: 0 };
    const base = bypassScene();
    base.actors = base.actors.filter(actor => actor.id !== 'obstacle');
    base.frames[0].actors = base.frames[0].actors.filter(actor => actor.id !== 'obstacle');
    let state = { ...createDriverSimulation(withEventActors(base, plan)), events: createEvents(plan, 'event') };
    for (let tick = 0; tick < 25; tick++) state = advanceDriver(state, stop, .1) as typeof state;
    expect(driverObservation(state).go_around_available).toBe(true);
    let maxOffset = 0;
    for (let tick = 0; tick < 300; tick++) {
      state = advanceDriver(state, goAround, .1) as typeof state;
      const frame = driverFrame(state), ego = frame.actors.find(actor => actor.id === 'ego')!;
      maxOffset = Math.max(maxOffset, ego.y);
      for (const actor of frame.actors.filter(actor => actor.id.startsWith('event-'))) {
        expect(footprintContact(ego, state.ego, actor, state.scenario.actors.find(definition => definition.id === actor.id)!)).toBe(false);
      }
      if (state.progress > 80) break;
    }
    expect(maxOffset).toBeGreaterThan(3.9); expect(state.progress).toBeGreaterThan(70);
  });

  it('can bypass the authored Vestry crash with surrounding traffic and retains its red-light stop', () => {
    const plan = preset(); plan.events[0].durationSeconds = 20;
    let state = { ...createDriverSimulation(withEventActors(demoScenario, plan)), events: createEvents(plan, 'event') };
    let decision = { ...accelerate, target_speed_mps: 8 }, began = false, rejoined = false, maxOffset = 0;
    for (let tick = 0; tick < 260; tick++) {
      if (tick % 10 === 0) {
        const observation = driverObservation(state);
        decision = observation.go_around_available ? goAround
          : observation.lead_gap_m !== null && observation.lead_gap_m < 35
            ? { ...stop, action: 'brake', target_speed_mps: Math.min(2.5, state.speed) } : { ...accelerate, target_speed_mps: 8 };
      }
      state = advanceDriver(state, decision, .1) as typeof state;
      began ||= Boolean(state.bypass);
      rejoined ||= began && !state.bypass;
      const frame = driverFrame(state), ego = frame.actors.find(actor => actor.id === 'ego')!;
      const center = routePosition(state.path, state.progress);
      maxOffset = Math.max(maxOffset, Math.hypot(ego.x - center.x, ego.y - center.y));
      for (const actor of frame.actors.filter(actor => actor.id.startsWith('event-'))) {
        expect(footprintContact(ego, state.ego, actor, state.scenario.actors.find(definition => definition.id === actor.id)!)).toBe(false);
      }
      if (state.time >= 15) expect(state.progress).toBeLessThanOrEqual(state.stopProgress! + 1e-8);
    }
    expect(began).toBe(true); expect(rejoined).toBe(true); expect(maxOffset).toBeGreaterThan(3.9);
    expect(state.speed).toBeLessThan(.1);
  });
});

describe('Gemini driver deterministic physics', () => {
  it('starts the demo at its existing ego location, leaving the source replay untouched', () => {
    const initial = sampleScenario(demoScenario, 0);
    const ego = initial.actors.find(actor => actor.id === 'ego')!;
    const simulation = createDriverSimulation(demoScenario);
    const driven = driverFrame(simulation).actors.find(actor => actor.id === 'ego')!;
    expect(simulation.progress).toBeCloseTo(84, 6);
    expect(driven.x).toBeCloseTo(ego.x, 8);
    expect(driven.y).toBeCloseTo(ego.y, 8);
    expect(driverObservation(simulation).remaining_m).toBeCloseTo(316, 6);
    advanceDriver(simulation, accelerate, 0.1);
    expect(sampleScenario(demoScenario, 0)).toEqual(initial);
  });

  it('moves from the model target rather than advancing an authored ego playback frame', () => {
    let moving = createDriverSimulation(streetScene()), waiting = createDriverSimulation(streetScene());
    for (let i = 0; i < 30; i++) {
      const previous = moving;
      moving = advanceDriver(moving, accelerate, 0.1);
      waiting = advanceDriver(waiting, stop, 0.1);
      expect(moving.speed - previous.speed).toBeLessThanOrEqual(MAX_ACCELERATION * 0.1 + 1e-9);
    }
    expect(moving.progress).toBeGreaterThan(6);
    expect(waiting.progress).toBe(0);
    expect(driverFrame(moving).actors[0].x).toBeCloseTo(moving.progress, 6);
    expect(sampleScenario(moving.scenario, moving.time).actors[0].x).toBe(0);
  });

  it('bounds timestep, acceleration, braking and never accelerates for a brake instruction', () => {
    let simulation = createDriverSimulation(streetScene());
    simulation = advanceDriver(simulation, accelerate, 20);
    expect(simulation.time).toBe(0.1);
    expect(simulation.speed).toBeCloseTo(MAX_ACCELERATION * 0.1);
    simulation = { ...simulation, speed: 8 };
    const braked = advanceDriver(simulation, stop, 0.1);
    expect(braked.speed).toBeCloseTo(8 - COMFORTABLE_BRAKING * 0.1);
    expect(advanceDriver(simulation, { ...accelerate, action: 'brake' }, 0.1).speed).toBeLessThanOrEqual(simulation.speed);
  });

  it('stops before the authored red line even when the model requests acceleration', () => {
    const scenario = { ...demoScenario, actors: demoScenario.actors.filter(actor => actor.role === 'ego'),
      frames: demoScenario.frames.map(frame => ({ ...frame, actors: frame.actors.filter(actor => actor.id === 'ego') })) };
    let simulation = createDriverSimulation(scenario);
    simulation = { ...simulation, time: 15, progress: simulation.stopProgress! - 20, speed: 7 };
    const stopProgress = simulation.stopProgress!;
    for (let i = 0; i < 100; i++) {
      simulation = advanceDriver(simulation, accelerate, 0.1);
      expect(simulation.progress).toBeLessThanOrEqual(stopProgress + 1e-9);
    }
    expect(simulation.speed).toBeLessThan(0.05);
    expect(driverObservation(simulation).signal).toBe('red');
    simulation = { ...simulation, time: 29 };
    expect(advanceDriver(simulation, accelerate, 0.1).speed).toBeGreaterThan(simulation.speed);
  });

  it('latches an amber stop decision and does not invent traffic lights for mapped routes', () => {
    let simulation = createDriverSimulation(demoScenario);
    simulation = { ...simulation, time: 12, progress: simulation.stopProgress! - 30, speed: 5 };
    simulation = advanceDriver(simulation, accelerate, 0.1);
    expect(simulation.signalStop).toBe(true);
    simulation = { ...simulation, progress: simulation.stopProgress! - 1, speed: 5 };
    expect(advanceDriver(simulation, accelerate, 0.1).signalStop).toBe(true);
    expect(driverObservation(createDriverSimulation(streetScene())).signal).toBe('unknown');
    expect(driverObservation(createDriverSimulation(streetScene())).stop_distance_m).toBeNull();
  });

  it('reports bumper gap and keeps the ego behind a stopped vehicle', () => {
    const scenario = streetScene();
    scenario.actors.push({ ...scenario.actors[0], id: 'leader', role: 'traffic', length: 5 });
    scenario.frames[0].actors.push({ id: 'leader', x: 20, y: 0, heading: 0, speed: 0, braking: true });
    let simulation = createDriverSimulation(scenario);
    expect(driverObservation(simulation).lead_gap_m).toBeCloseTo(15.05);
    expect(driverObservation(simulation).lead_speed_mps).toBe(0);
    for (let i = 0; i < 300; i++) simulation = advanceDriver(simulation, accelerate, 0.1);
    expect(simulation.progress).toBeLessThanOrEqual(12.05);
    expect(simulation.speed).toBeLessThan(0.1);
    expect(driverObservation(simulation).lead_gap_m).toBeGreaterThanOrEqual(3 - 1e-9);
  });

  it('follows the selected polyline, reaches its endpoint and never wraps', () => {
    let simulation = createDriverSimulation(streetScene([[0, 0], [15, 0], [15, 12]]));
    for (let i = 0; i < 1000; i++) simulation = advanceDriver(simulation, accelerate, 0.1);
    const ego = driverFrame(simulation).actors[0];
    expect(ego.x).toBeCloseTo(15, 3); expect(ego.y).toBeCloseTo(12, 3);
    expect(simulation.progress).toBeLessThanOrEqual(27);
    expect(routeComplete(simulation)).toBe(true);
    expect(routePosition(simulation.path, 50).y).toBe(12);
  });

  it('samples background vehicles using simulation time and disallows a stationary location preview', () => {
    let simulation = createDriverSimulation(demoScenario);
    simulation = advanceDriver(simulation, stop, 0.1);
    const expected = sampleScenario(demoScenario, 0.1).actors.filter(actor => actor.id !== 'ego');
    expect(driverFrame(simulation).actors.filter(actor => actor.id !== 'ego')).toEqual(expected);
    expect(driverEligibility({ ...streetScene([[0, 0]]), motionSource: 'location-preview' })).toMatch(/Plan a street route/);
  });
});
