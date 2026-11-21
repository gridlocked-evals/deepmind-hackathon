import { afterEach, describe, expect, it, vi } from 'vitest';
import { MercatorCoordinate } from 'maplibre-gl';
import { advancePlaybackTime } from '../hooks/usePlayback';
import { sampleScenario } from '../scene/DemoScenarioProvider';
import { createIntersectionScenario, createRouteScenario, localPoint } from './routeScenario';
import { fetchStreetRoute, parseStreetRoute } from './routing';
import type { NavigationPoint, PlannedRoute } from './types';

const start: NavigationPoint = { coordinates: [-74.01, 40.72], label: 'Selected intersection' };
const end: NavigationPoint = { coordinates: [-74.008, 40.722], label: 'Destination' };
const route: PlannedRoute = {
  coordinates: [start.coordinates, [-74.008, 40.72], end.coordinates],
  distanceMeters: 400, durationSeconds: 50, source: 'test street geometry', steps: [],
};
const payload = () => ({
  code: 'Ok', routes: [{ distance: 400, duration: 50, geometry: { type: 'LineString', coordinates: route.coordinates },
    legs: [{ steps: [{ name: 'Test Street', distance: 400, maneuver: { type: 'depart' } }] }] }],
  waypoints: [{ distance: 2 }, { distance: 3 }],
});

afterEach(() => vi.unstubAllGlobals());

describe('street routing', () => {
  it('keeps provider geometry and directions, and rejects fabricated/incomplete routes', () => {
    expect(parseStreetRoute(payload())).toMatchObject({ coordinates: route.coordinates, distanceMeters: 400,
      durationSeconds: 50, steps: [{ label: 'Start · Test Street', distanceMeters: 400 }] });
    expect(() => parseStreetRoute({ code: 'NoRoute' })).toThrow('No driving route');
    expect(() => parseStreetRoute({ ...payload(), routes: [{ distance: 400, duration: 50, geometry: { type: 'LineString', coordinates: [[0, 0], [1, 1]] } }] })).toThrow('incomplete');
    expect(() => parseStreetRoute({ ...payload(), waypoints: [{ distance: 201 }] })).toThrow('too far');
    expect(() => parseStreetRoute({ ...payload(), routes: [{ ...payload().routes[0], distance: 90_000 }] })).toThrow('shorter');
    expect(() => parseStreetRoute({ ...payload(), routes: [{ ...payload().routes[0], geometry: { type: 'LineString', coordinates: [start.coordinates, start.coordinates] } }] })).toThrow('no drivable distance');
  });

  it('requests road routing with bounded snapping and forwards cancellation', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => payload() });
    vi.stubGlobal('fetch', fetch);
    expect((await fetchStreetRoute(start, end)).coordinates).toEqual(route.coordinates);
    expect(fetch.mock.calls[0][0]).toContain('/route/v1/driving/-74.010000,40.720000;-74.008000,40.722000');
    expect(fetch.mock.calls[0][0]).toContain('radiuses=200;200');
    const controller = new AbortController(); controller.abort();
    await expect(fetchStreetRoute(start, end, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('does not request a route for identical or out-of-city points', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    await expect(fetchStreetRoute(start, start)).rejects.toThrow('farther');
    await expect(fetchStreetRoute(start, { ...end, coordinates: [0, 0] })).rejects.toThrow('within New York City');
    expect(fetch).not.toHaveBeenCalled();
  });

  it('surfaces transport failures without replacing road geometry with a straight line', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    await expect(fetchStreetRoute(start, end)).rejects.toThrow('could not connect');
  });

  it('cancels an in-flight response before accepting stale geometry', async () => {
    const controller = new AbortController();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => { controller.abort(); return payload(); } }));
    await expect(fetchStreetRoute(start, end, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
  });
});

describe('geographic route simulation', () => {
  it('matches the map renderer metre conversion across borough-scale distances', () => {
    const origin: [number, number] = [-74.2, 40.55];
    const destination: [number, number] = [-73.85, 40.88];
    const [x, y] = localPoint(origin, destination);
    const anchor = MercatorCoordinate.fromLngLat(origin);
    const scale = anchor.meterInMercatorCoordinateUnits();
    const rendered = new MercatorCoordinate(anchor.x + x * scale, anchor.y - y * scale).toLngLat();
    expect(rendered.lng).toBeCloseTo(destination[0], 10);
    expect(rendered.lat).toBeCloseTo(destination[1], 10);
  });

  it('keeps a borough-scale journey bounded while preserving its destination', () => {
    const coordinates: [number, number][] = [[-74.2, 40.55], [-73.85, 40.88]];
    const scenario = createRouteScenario({ ...route, coordinates, distanceMeters: 47_000 }, start, 'camera');
    expect(scenario.geometry.route.length).toBeLessThan(15_000);
    expect(scenario.frames.length).toBeLessThan(30_000);
    expect(scenario.frames.at(-1)?.actors[0].x).toBeCloseTo(localPoint(coordinates[0], coordinates[1])[0], 5);
    expect(scenario.frames.at(-1)?.actors[0].speed).toBe(0);
  });

  it('starts at the road origin and arrives stationary at the actual destination', () => {
    const scenario = createRouteScenario(route, start, 'camera');
    expect(scenario.geometryKind).toBe('street-route');
    expect(scenario.motionSource).toBe('driving-route');
    expect(scenario.playbackMode).toBe('once');
    expect(scenario.actors).toHaveLength(1);
    expect(scenario.geometry.roads).toEqual([]);
    expect(scenario.frames[0].actors[0]).toMatchObject({ x: 0, y: 0, speed: 0 });
    const final = sampleScenario(scenario, scenario.duration).actors[0];
    const destination = localPoint(scenario.origin, end.coordinates);
    expect(final.x).toBeCloseTo(destination[0], 5);
    expect(final.y).toBeCloseTo(destination[1], 5);
    expect(final.speed).toBe(0);
    expect(scenario.duration).toBeGreaterThan(30);
  });

  it('stays on streets, anticipates a 90-degree turn, and bounds acceleration', () => {
    const scenario = createRouteScenario(route, start, 'camera');
    const corner = localPoint(start.coordinates, route.coordinates[1]);
    let cornerSpeed = Infinity;
    let maxSpeed = 0;
    for (let index = 0; index < scenario.frames.length; index++) {
      const frame = scenario.frames[index], actor = frame.actors[0];
      expect(Math.min(Math.abs(actor.y), Math.abs(actor.x - corner[0]))).toBeLessThan(0.001);
      expect(actor.speed).toBeGreaterThanOrEqual(0);
      expect(actor.speed).toBeLessThanOrEqual(11.176 + 0.001);
      maxSpeed = Math.max(maxSpeed, actor.speed);
      if (Math.hypot(actor.x - corner[0], actor.y - corner[1]) < 1.5) cornerSpeed = Math.min(cornerSpeed, actor.speed);
      if (index) {
        const before = scenario.frames[index - 1];
        const acceleration = (actor.speed - before.actors[0].speed) / (frame.time - before.time);
        expect(acceleration).toBeGreaterThanOrEqual(-2.2 - 0.001);
        expect(acceleration).toBeLessThanOrEqual(1.3 + 0.001);
      }
    }
    expect(maxSpeed).toBeGreaterThan(10);
    expect(cornerSpeed).toBeLessThan(4);
  });

  it('uses deterministic random access and handles short and duplicate segments', () => {
    const scenario = createRouteScenario(route, start, 'camera');
    const first = sampleScenario(scenario, 12.53);
    sampleScenario(scenario, 25);
    expect(sampleScenario(scenario, 12.53)).toEqual(first);
    expect(createRouteScenario(route, start, 'camera')).toEqual(scenario);
    const tiny = createRouteScenario({ ...route, coordinates: [start.coordinates, start.coordinates, [-74.00999, 40.72]] }, start, 'camera');
    expect(tiny.frames.every(frame => frame.actors.every(actor => Number.isFinite(actor.speed)))).toBe(true);
    expect(tiny.frames.at(-1)?.actors[0].speed).toBe(0);
  });

  it('previews a picked street from its selection and never fabricates an intersection', () => {
    const located = createIntersectionScenario(start, 'camera');
    expect(located.origin).toEqual(start.coordinates);
    expect(located.motionSource).toBe('location-preview');
    expect(located.geometry.route).toHaveLength(1);
    expect(located.frames.every(frame => frame.actors[0].speed === 0)).toBe(true);
    const street = createIntersectionScenario({ ...start, streetPath: route.coordinates }, 'camera');
    expect(street.origin).toEqual(start.coordinates);
    expect(street.motionSource).toBe('street-preview');
    expect(street.geometry.route.length).toBeGreaterThan(2);
    expect(street.frames.some(frame => frame.actors[0].speed > 0)).toBe(true);
    expect(street.geometry.crosswalks).toEqual([]);
  });

  it('stops once at arrival, while the authored demonstration continues to loop', () => {
    expect(advancePlaybackTime(59.9, 0.2, 60, 'once')).toBe(60);
    expect(advancePlaybackTime(59.9, 0.2, 60, 'loop')).toBeCloseTo(0.1);
    expect(advancePlaybackTime(59.9, 0.2, 60, undefined)).toBeCloseTo(0.1);
    expect(advancePlaybackTime(0, 0.2, 0, 'once')).toBe(0);
  });
});
