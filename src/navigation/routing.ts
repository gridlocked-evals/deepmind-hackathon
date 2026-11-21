import type { LngLat } from '../types';
import type { NavigationPoint, PlannedRoute } from './types';

const ROUTING_ENDPOINT = 'https://router.project-osrm.org/route/v1/driving';
const MAX_ROUTE_METERS = 80_000;

export function isNycCoordinate(value: unknown): value is LngLat {
  return Array.isArray(value) && value.length === 2 && value.every(Number.isFinite)
    && value[0] >= -74.3 && value[0] <= -73.65 && value[1] >= 40.45 && value[1] <= 40.95;
}

export function parseStreetRoute(value: unknown): PlannedRoute {
  if (!value || typeof value !== 'object') throw new Error('The routing service returned an invalid route.');
  const payload = value as { code?: string; routes?: unknown[]; waypoints?: { distance?: number }[] };
  if (payload.code === 'NoRoute' || payload.code === 'NoSegment') {
    throw new Error('No driving route connects these points. Choose points closer to a street.');
  }
  if (payload.code !== 'Ok' || !Array.isArray(payload.routes) || !payload.routes[0]) {
    throw new Error('The routing service could not plan this trip. Try another intersection.');
  }
  const route = payload.routes[0] as {
    distance?: number; duration?: number;
    geometry?: { type?: string; coordinates?: unknown[] };
    legs?: { steps?: { name?: string; distance?: number; maneuver?: { type?: string; modifier?: string } }[] }[];
  };
  const coordinates = route.geometry?.coordinates;
  if (route.geometry?.type !== 'LineString' || !Array.isArray(coordinates) || coordinates.length < 2
    || coordinates.length > 50_000 || !coordinates.every(isNycCoordinate)
    || !Number.isFinite(route.distance) || route.distance! < 2
    || !Number.isFinite(route.duration) || route.duration! <= 0) {
    throw new Error('The routing service returned incomplete street geometry. Choose different points.');
  }
  if (route.distance! > MAX_ROUTE_METERS) throw new Error('Choose a shorter route, under 80 km.');
  // Bound actual geometry as well as the provider's summary before allocating simulation frames.
  let geometryMeters = 0;
  for (let index = 1; index < coordinates.length; index++) {
    const a = coordinates[index - 1], b = coordinates[index];
    const radians = Math.PI / 180;
    geometryMeters += 6_371_008.8 * Math.hypot((b[0] - a[0]) * radians * Math.cos((a[1] + b[1]) / 2 * radians), (b[1] - a[1]) * radians);
    if (geometryMeters > MAX_ROUTE_METERS * 1.01) throw new Error('Choose a shorter route, under 80 km.');
  }
  if (geometryMeters < 2) throw new Error('The route has no drivable distance. Choose different points.');
  if (!Array.isArray(payload.waypoints) || payload.waypoints.length !== 2
    || payload.waypoints.some(point => !point || !Number.isFinite(point.distance) || point.distance! < 0 || point.distance! > 200)) {
    throw new Error('A selected point is too far from a drivable street. Choose a nearby intersection.');
  }
  const steps = Array.isArray(route.legs) ? route.legs.flatMap(leg => leg && Array.isArray(leg.steps) ? leg.steps : [])
    .filter(step => step && Number.isFinite(step.distance) && step.distance! >= 0)
    .map(step => {
      const maneuver = step.maneuver?.type;
      const action = maneuver === 'depart' ? 'Start' : maneuver === 'arrive' ? 'Arrive'
        : typeof step.maneuver?.modifier === 'string' ? `Turn ${step.maneuver.modifier}` : 'Continue';
      return { label: typeof step.name === 'string' && step.name ? `${action} · ${step.name}` : action, distanceMeters: step.distance! };
    }) : [];
  return {
    coordinates: coordinates.map(point => [...point] as LngLat),
    distanceMeters: route.distance!, durationSeconds: route.duration!,
    source: 'OpenStreetMap / OSRM', steps,
  };
}

/** OSRM respects its mapped driving network, including directionality. Never draw a straight-line fallback. */
export async function fetchStreetRoute(start: NavigationPoint, end: NavigationPoint, signal?: AbortSignal): Promise<PlannedRoute> {
  if (!isNycCoordinate(start.coordinates) || !isNycCoordinate(end.coordinates)) {
    throw new Error('Choose a start and destination within New York City.');
  }
  if (Math.hypot(start.coordinates[0] - end.coordinates[0], start.coordinates[1] - end.coordinates[1]) < 0.00004) {
    throw new Error('Choose a destination farther from the starting point.');
  }
  signal?.throwIfAborted();
  const controller = new AbortController();
  const cancel = () => controller.abort(signal?.reason);
  signal?.addEventListener('abort', cancel, { once: true });
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 15_000);
  const points = [start.coordinates, end.coordinates].map(point => point.map(value => value.toFixed(6)).join(',')).join(';');
  try {
    const response = await fetch(`${ROUTING_ENDPOINT}/${points}?overview=full&geometries=geojson&steps=true&radiuses=200;200`, {
      signal: controller.signal, headers: { Accept: 'application/json' },
    });
    if (!response.ok) throw new Error('Street routing is unavailable. Try again in a moment.');
    const payload: unknown = await response.json();
    controller.signal.throwIfAborted();
    return parseStreetRoute(payload);
  } catch (error) {
    if (signal?.aborted) throw signal.reason ?? new DOMException('Route request cancelled', 'AbortError');
    if (timedOut) throw new Error('Street routing took too long. Try again.');
    if (error instanceof TypeError) throw new Error('Street routing could not connect. Check your connection and retry.');
    throw error;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', cancel);
  }
}
