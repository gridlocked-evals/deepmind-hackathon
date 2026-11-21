import { expect, it } from 'vitest';
import { createDriverSimulation, createPath, projectOntoRoute } from '../agent/physics';
import { createRouteScenario } from '../navigation/routeScenario';
import { ADVERSARY_PLAN } from '../scenarios/adversary';
import { withEventActors } from '../scenarios/events';
import { finishTrace, replayScene } from '../scenarios/trace';
import { advanceOpening, createOpening, openingStart } from './opening';

function runOpening() {
  const origin = { label: 'NYC fixture', coordinates: [-74, 40.72] as [number, number] };
  const route = { coordinates: [origin.coordinates, [-74, 40.73] as [number, number]], distanceMeters: 1100,
    durationSeconds: 120, source: 'Fixture', steps: [] };
  const state = createDriverSimulation(withEventActors(createRouteScenario(route, origin, ''), ADVERSARY_PLAN));
  const run = createOpening(state, 'scripted-fixture');
  for (let i = 0; i < 550 && !run.result.complete; i++) advanceOpening(run);
  finishTrace(run.trace, run.state, 'scripted-complete');
  return run;
}

it('always physically passes a crash, rejoins, then passes a stalled vehicle slowly before cleanup', () => {
  const { trace, result, state } = runOpening();
  expect(result.complete).toBe(true);
  expect(result.events.map(event => event.type)).toEqual(['car-crash', 'stalled-vehicle']);
  expect(result.events[0].appliedAt).toBe(0);
  expect(result.events[1].appliedAt).toBe(result.events[0].rejoinedAt);
  expect(trace.eventLog.filter(row => row.kind === 'scripted_collision')).toHaveLength(1);
  expect(trace.telemetry.every(row => row.egoContacts.length === 0)).toBe(true);
  for (const event of result.events) {
    const anchor = trace.appliedEvents.find(row => row.id === event.id)!.anchorProgress;
    const alongside = trace.frames.filter((_, index) => Math.abs(trace.telemetry[index].progress - anchor) < 6);
    expect(alongside.length).toBeGreaterThan(20);
    for (const frame of alongside) {
      const ego = frame.actors.find(actor => actor.id === 'ego')!;
      expect(ego.speed).toBeGreaterThan(2.9); expect(ego.speed).toBeLessThan(3.1);
      expect(projectOntoRoute(state.path, [ego.x, ego.y]).lateralDistance).toBeCloseTo(5);
      expect(frame.actors.some(actor => actor.id.startsWith(`event-${event.id}-`))).toBe(true);
    }
    const rejoin = trace.frames.find(frame => frame.time === event.rejoinedAt)!;
    const ego = rejoin.actors.find(actor => actor.id === 'ego')!;
    expect(projectOntoRoute(state.path, [ego.x, ego.y]).lateralDistance).toBeLessThan(.01);
    expect(event.passedAt).toBeLessThan(event.rejoinedAt!);
    expect(event.rejoinedAt).toBeLessThan(trace.eventLog.find(row => row.eventId === event.id && row.kind === 'cleared')!.time);
  }
});

it('exports an honest deterministic replay with no model decisions or adversary score', () => {
  const first = runOpening(), second = runOpening();
  expect(first.trace.frames).toEqual(second.trace.frames);
  expect(first.trace).toMatchObject({ modelSource: 'scripted-demo', modelPolicy: { service: 'scripted', modelId: null }, steps: [] });
  expect(first.trace.adversary).toBeUndefined();
  expect(replayScene(JSON.parse(JSON.stringify(first.trace))).frames).toEqual(first.trace.frames);
  const frames = first.trace.frames;
  for (let i = 1; i < frames.length; i++) {
    const a = frames[i - 1].actors.find(actor => actor.id === 'ego')!, b = frames[i].actors.find(actor => actor.id === 'ego')!;
    expect(Math.hypot(b.x - a.x, b.y - a.y)).toBeLessThan(.7);
  }
});

it('chooses a straight street section after bends and rejects insufficient geometry', () => {
  expect(openingStart(createPath([[0, 0], [60, 0], [60, 300]]))).toBe(60);
  expect(() => openingStart(createPath([[0, 0], [100, 0]]))).toThrow('straight 180 m');
});
