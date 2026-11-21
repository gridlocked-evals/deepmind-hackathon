import type { ActorDefinition, LngLat, ScenarioBundle, SimulationFrame, Vec2 } from '../types';
import type { NavigationPoint, PlannedRoute } from './types';

// MapLibre uses the mean Earth radius, not EPSG:3857's equatorial radius.
// Matching its metre conversion avoids accumulating drift on long city routes.
const EARTH_CIRCUMFERENCE = 2 * Math.PI * 6_371_008.8;
const CRUISE_SPEED = 11.176; // Assumed 25 mph for a preview, not a surveyed speed limit.
const ACCELERATION = 1.3;
const BRAKING = 2.2;
const LATERAL_ACCELERATION = 1.5;
const FRAME_STEP = 0.2;
const ego: ActorDefinition = {
  id: 'ego', kind: 'car', label: 'Route vehicle', role: 'ego', length: 4.9,
  width: 1.98, height: 1.86, color: '#f4f8f8', routeId: 'street-route',
};

function mercator(point: LngLat): Vec2 {
  return [(point[0] + 180) / 360, (180 - (180 / Math.PI * Math.log(Math.tan(Math.PI / 4 + point[1] * Math.PI / 360)))) / 360];
}

/** Matches the renderer's Mercator origin/scale so long routes stay on the basemap. */
export function localPoint(origin: LngLat, point: LngLat): Vec2 {
  const anchor = mercator(origin), next = mercator(point);
  const metres = EARTH_CIRCUMFERENCE * Math.cos(origin[1] * Math.PI / 180);
  return [(next[0] - anchor[0]) * metres, (anchor[1] - next[1]) * metres];
}

function hashPath(points: LngLat[]): number {
  let hash = 2166136261;
  for (const point of points) for (const character of point.join(',')) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619);
  return hash >>> 0;
}

function resample(points: Vec2[]): Vec2[] {
  const sampled: Vec2[] = [[...points[0]]];
  for (let index = 1; index < points.length; index++) {
    const a = sampled[sampled.length - 1], b = points[index];
    const distance = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (distance < 0.05) continue;
    const segments = Math.ceil(distance / 4);
    for (let segment = 1; segment <= segments; segment++) {
      const fraction = segment / segments;
      sampled.push([a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction]);
    }
  }
  return sampled;
}

function makeFrames(points: Vec2[]): SimulationFrame[] {
  if (points.length < 2) return [0, 1].map(time => ({ time, signal: 'green', actors: [{ id: 'ego', x: 0, y: 0, heading: 0, speed: 0, braking: false }] }));
  const distances = points.map((point, index) => index ? Math.hypot(point[0] - points[index - 1][0], point[1] - points[index - 1][1]) : 0);
  const speeds = points.map((point, index) => {
    if (index === 0 || index === points.length - 1) return 0;
    const before = points[index - 1], after = points[index + 1];
    const incoming = Math.atan2(point[1] - before[1], point[0] - before[0]);
    const outgoing = Math.atan2(after[1] - point[1], after[0] - point[0]);
    const angle = Math.abs(Math.atan2(Math.sin(outgoing - incoming), Math.cos(outgoing - incoming)));
    const radius = (distances[index] + distances[index + 1]) / (2 * Math.max(angle, 0.001));
    return Math.min(CRUISE_SPEED, Math.max(1.5, Math.sqrt(LATERAL_ACCELERATION * radius)));
  });
  // A braking pass anticipates corners and the final stop; a forward pass bounds acceleration.
  for (let index = speeds.length - 2; index >= 0; index--) speeds[index] = Math.min(speeds[index], Math.sqrt(speeds[index + 1] ** 2 + 2 * BRAKING * distances[index + 1]));
  for (let index = 1; index < speeds.length; index++) speeds[index] = Math.min(speeds[index], Math.sqrt(speeds[index - 1] ** 2 + 2 * ACCELERATION * distances[index]));
  const times = [0];
  for (let index = 1; index < points.length; index++) {
    // Resampling ensures a nonzero interior speed even on short two-point routes.
    times.push(times[index - 1] + 2 * distances[index] / Math.max(0.1, speeds[index - 1] + speeds[index]));
  }
  const duration = times[times.length - 1];
  const frames: SimulationFrame[] = [];
  let segment = 1;
  for (let tick = 0; tick <= Math.ceil(duration / FRAME_STEP); tick++) {
    const time = Math.min(tick * FRAME_STEP, duration);
    while (segment < points.length - 1 && times[segment] < time) segment++;
    const a = points[segment - 1], b = points[segment];
    const elapsed = time - times[segment - 1], segmentTime = times[segment] - times[segment - 1];
    const acceleration = (speeds[segment] - speeds[segment - 1]) / segmentTime;
    const traveled = speeds[segment - 1] * elapsed + 0.5 * acceleration * elapsed ** 2;
    const fraction = Math.min(1, Math.max(0, traveled / distances[segment]));
    const atEnd = time === duration;
    frames.push({ time, signal: 'green', actors: [{
      id: 'ego', x: atEnd ? b[0] : a[0] + (b[0] - a[0]) * fraction,
      y: atEnd ? b[1] : a[1] + (b[1] - a[1]) * fraction,
      heading: Math.atan2(b[1] - a[1], b[0] - a[0]),
      speed: atEnd ? 0 : Math.max(0, speeds[segment - 1] + acceleration * elapsed), braking: acceleration < -0.1,
    }] });
  }
  return frames;
}

export function createRouteScenario(route: PlannedRoute, start: NavigationPoint, cameraId: string): ScenarioBundle {
  if (route.coordinates.length < 2) throw new Error('A route needs at least two street coordinates.');
  const origin = route.coordinates[0];
  let points = resample(route.coordinates.map(point => localPoint(origin, point)));
  if (points.length === 2) points = [points[0], [(points[0][0] + points[1][0]) / 2, (points[0][1] + points[1][1]) / 2], points[1]];
  const frames = makeFrames(points);
  const seed = hashPath(route.coordinates);
  return {
    id: `street-route-${seed}`, version: '1', label: start.label, provenance: 'demo',
    seed, cameraId, origin, duration: frames[frames.length - 1].time,
    playbackMode: 'once', geometryKind: 'street-route', motionSource: 'driving-route',
    // Road widths, lanes, crosswalks, traffic and signals are unknown until image processing supplies evidence.
    geometry: { roads: [], sidewalks: [], crosswalks: [], lanes: [], route: points },
    actors: [{ ...ego }], frames,
  };
}

export function createIntersectionScenario(point: NavigationPoint, cameraId: string): ScenarioBundle {
  const path = point.streetPath;
  if (path && path.length > 1) {
    // Project the selection onto its street, then preview the longer contiguous side (not legal directions).
    const local = path.map(coordinate => localPoint(point.coordinates, coordinate));
    let nearest = { index: 0, fraction: 0, distance: Infinity, coordinates: point.coordinates };
    for (let index = 0; index < local.length - 1; index++) {
      const a = local[index], b = local[index + 1], dx = b[0] - a[0], dy = b[1] - a[1];
      const fraction = Math.max(0, Math.min(1, -(a[0] * dx + a[1] * dy) / (dx * dx + dy * dy || 1)));
      const distance = Math.hypot(a[0] + fraction * dx, a[1] + fraction * dy);
      if (distance < nearest.distance) nearest = { index, fraction, distance,
        coordinates: [path[index][0] + fraction * (path[index + 1][0] - path[index][0]), path[index][1] + fraction * (path[index + 1][1] - path[index][1])] };
    }
    if (nearest.distance < 40) {
      const ahead = [nearest.coordinates, ...path.slice(nearest.index + 1)];
      const behind = [nearest.coordinates, ...path.slice(0, nearest.index + 1).reverse()];
      const length = (coordinates: LngLat[]) => coordinates.reduce((total, coordinate, index) => index ? total + Math.hypot(...localPoint(coordinates[index - 1], coordinate)) : 0, 0);
      const candidate = length(ahead) >= length(behind) ? ahead : behind;
      const coordinates: LngLat[] = [candidate[0]];
      let distance = 0;
      for (let index = 1; index < candidate.length && distance < 250; index++) {
        const segment = Math.hypot(...localPoint(candidate[index - 1], candidate[index]));
        if (segment < 0.05) continue;
        const fraction = Math.min(1, (250 - distance) / segment);
        coordinates.push([candidate[index - 1][0] + (candidate[index][0] - candidate[index - 1][0]) * fraction,
          candidate[index - 1][1] + (candidate[index][1] - candidate[index - 1][1]) * fraction]);
        distance += segment * fraction;
      }
      if (distance > 2) return { ...createRouteScenario({ coordinates, distanceMeters: distance, durationSeconds: distance / 6,
        source: 'Basemap street preview · direction unverified', steps: [] }, point, cameraId), motionSource: 'street-preview' };
    }
  }
  // Without street evidence, preview the selected location rather than inventing road geometry.
  const seed = hashPath([point.coordinates]);
  return {
    id: `location-${seed}`, version: '1', label: point.label, provenance: 'demo', seed, cameraId,
    origin: point.coordinates, duration: 1, playbackMode: 'once', geometryKind: 'street-route', motionSource: 'location-preview',
    geometry: { roads: [], sidewalks: [], crosswalks: [], lanes: [], route: [[0, 0]] },
    actors: [{ ...ego }], frames: makeFrames([[0, 0]]),
  };
}
