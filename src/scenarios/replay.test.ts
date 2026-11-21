import { describe, expect, it } from 'vitest';
import { createDriverSimulation } from '../agent/physics';
import { createRouteScenario } from '../navigation/routeScenario';
import { parseStreetRoute } from '../navigation/routing';
import type { LngLat } from '../types';
import { createEvents, preset, withEventActors } from './events';
import { createTrace, replayScene } from './trace';

function routeTrace(coordinates: LngLat[]) {
  const route = parseStreetRoute({ code: 'Ok', waypoints: [{ distance: 0 }, { distance: 0 }],
    routes: [{ distance: 47_000, duration: 3600, geometry: { type: 'LineString', coordinates } }] });
  const scene = createRouteScenario(route, { coordinates: coordinates[0], label: 'Planned route' }, 'camera');
  const plan = preset();
  return createTrace({ ...createDriverSimulation(withEventActors(scene, plan)), events: createEvents(plan, 'baseline') },
    'fixture-replay-bounds', 'test-fixture');
}

describe('replay compatibility with accepted street routes', () => {
  it('replays an exported borough-scale route with coordinates beyond 20 km', () => {
    const trace = routeTrace([[-74.2, 40.55], [-73.85, 40.88]]);
    expect(trace.scene.geometry.route.some(point => point.some(n => Math.abs(n) > 20_000))).toBe(true);
    expect(trace.scene.geometry.route.length).toBeGreaterThan(5000);
    const replay = replayScene(JSON.parse(JSON.stringify(trace)));
    expect(replay.geometry.route).toEqual(trace.scene.geometry.route);
    expect(replay.frames).toEqual(trace.frames);
  });

  it('replays dense accepted route geometry without discarding points', () => {
    const coordinates: LngLat[] = Array.from({ length: 6001 }, (_, i) => [-74.01 + i / 1_000_000, 40.72]);
    const trace = routeTrace(coordinates);
    expect(trace.scene.geometry.route.length).toBe(6001);
    expect(replayScene(trace).geometry.route).toEqual(trace.scene.geometry.route);
  });

  it('still rejects excessive geometry and non-finite coordinates', () => {
    const trace = routeTrace([[-74.01, 40.72], [-74.009, 40.72]]);
    trace.scene.geometry.route = Array.from({ length: 75_001 }, () => [0, 0]);
    expect(() => replayScene(trace)).toThrow('Invalid replay coordinates');
    trace.scene.geometry.route = [[0, 0], [Infinity, 0]];
    expect(() => replayScene(trace)).toThrow('Invalid replay coordinates');
  });
});
