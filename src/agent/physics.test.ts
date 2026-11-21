import { describe, expect, it } from 'vitest';
import { demoScenario, sampleScenario } from '../scene/DemoScenarioProvider';
import type { ScenarioBundle } from '../types';
import { advanceDriver, COMFORTABLE_BRAKING, createDriverSimulation, driverEligibility, driverFrame, driverObservation, MAX_ACCELERATION, routeComplete, routePosition } from './physics';
import { SPEED_LIMIT_MPS } from './types';
import type { DriverDecision } from './types';

const accelerate: DriverDecision = { action: 'accelerate', target_speed_mps: SPEED_LIMIT_MPS, reason: 'Proceed along the route.', confidence: 0.9 };
const stop: DriverDecision = { action: 'stop', target_speed_mps: 0, reason: 'Wait here.', confidence: 0.9 };

function streetScene(points: [number, number][] = [[0, 0], [100, 0]]): ScenarioBundle {
  return { ...demoScenario, id: 'test-route', label: 'Test route', geometryKind: 'street-route', motionSource: 'driving-route',
    geometry: { roads: [], sidewalks: [], crosswalks: [], lanes: [], route: points },
    actors: [{ ...demoScenario.actors.find(actor => actor.role === 'ego')!, routeId: 'test-route' }],
    frames: [{ time: 0, signal: 'green', actors: [{ id: 'ego', x: 0, y: 0, heading: 0, speed: 0, braking: false }] }],
  };
}

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
