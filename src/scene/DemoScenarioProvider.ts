import type { ActorDefinition, ActorState, ScenarioBundle, ScenarioProvider, SimulationFrame, Vec2 } from '../types';

/** Geographic illustration, not geometry reconstructed from the camera image.
 * x points east, y north, distances are metres; heading is CCW from east.
 * West Street's local northbound tangent is approximately 8 degrees east of north.
 */
export const STREET_BEARING = 8 * Math.PI / 180;
export const STREET_HEADING = Math.PI / 2 - STREET_BEARING;
export const DEMO_SCENE_ID = 'vestry-west-st-demo';
export const ROAD_MIN = -195;
export const ROAD_MAX = 205;
const STEP = 0.1;

export function streetPoint(across: number, along: number): Vec2 {
  return [across * Math.cos(STREET_BEARING) + along * Math.sin(STREET_BEARING),
    -across * Math.sin(STREET_BEARING) + along * Math.cos(STREET_BEARING)];
}

function rectangle(left: number, bottom: number, right: number, top: number): Vec2[] {
  return [[left, bottom], [right, bottom], [right, top], [left, top]].map(([u, v]) => streetPoint(u, v));
}

function randomGenerator(seed: number) {
  let state = seed >>> 0;
  return () => {
    state ^= state << 13; state ^= state >>> 17; state ^= state << 5;
    return (state >>> 0) / 4294967296;
  };
}

/** One authored light program. Amber stops vehicles that still have braking room. */
export function demoSignal(time: number): SimulationFrame['signal'] {
  if (time < 12 || time >= 29) return 'green';
  if (time < 15) return 'amber';
  return 'red';
}

interface MovingActor {
  definition: ActorDefinition;
  across: number;
  along: number;
  direction: 1 | -1;
  speed: number;
  desiredSpeed: number;
  acceleration: number;
  signalStop: boolean;
}

/** A deterministic, bounded car-following illustration; this is not a traffic model fit. */
export function createDemoScenario(seed = 32998): ScenarioBundle {
  const random = randomGenerator(seed);
  const moving: MovingActor[] = [];
  for (let lane = 0; lane < 6; lane++) {
    const direction = lane < 3 ? 1 : -1;
    const across = direction * (4 + (lane % 3) * 4);
    for (let index = 0; index < 4; index++) {
      const kind = lane === 3 && index === 2 ? 'bus' : (lane + index) % 6 === 0 ? 'van' : 'car';
      const definition: ActorDefinition = {
        id: `traffic-${lane}-${index}`, kind, label: kind === 'bus' ? 'City bus' : kind === 'van' ? 'Delivery van' : 'Traffic vehicle',
        role: 'traffic', length: kind === 'bus' ? 10.8 : kind === 'van' ? 5.3 : 4.55,
        width: kind === 'bus' ? 2.45 : 1.85, height: kind === 'bus' ? 3.1 : kind === 'van' ? 2.25 : 1.55,
        color: ['#276da0', '#1d537e', '#388bb6', '#315f84'][index], routeId: `lane-${lane}`,
      };
      // Fixed separation with seeded, small speed variations. Lane 1 reserves space for ego.
      const starts = lane === 1 ? [-176, -59, 37, 126] : [-157, -67, 36, 130];
      moving.push({ definition, across, along: starts[index], direction, speed: 5.8,
        desiredSpeed: 6.5 + random() * 2.1, acceleration: 0, signalStop: false });
    }
  }
  const ego: ActorDefinition = { id: 'ego', kind: 'car', label: 'Gridlocked 01', role: 'ego',
    length: 4.9, width: 1.98, height: 1.86, color: '#f4f8f8', routeId: 'lane-1' };
  moving.push({ definition: ego, across: 8, along: -111, direction: 1, speed: 6.2, desiredSpeed: 6.6, acceleration: 0, signalStop: false });

  const frames: SimulationFrame[] = [];
  for (let tick = 0; tick <= 600; tick++) {
    const time = tick * STEP;
    const signal = demoSignal(time);
    const actors: ActorState[] = moving.map(actor => {
      const [x, y] = streetPoint(actor.across, actor.along);
      return { id: actor.definition.id, x, y,
        heading: STREET_HEADING + (actor.direction === -1 ? Math.PI : 0),
        speed: actor.speed, braking: actor.acceleration < -0.25 || actor.speed < 0.2 };
    });
    frames.push({ time, actors, signal });
    if (tick === 600) break;

    // Compute every acceleration from the same frame, then integrate together.
    for (const actor of moving) {
      const leader = moving.filter(other => other !== actor && other.across === actor.across)
        .map(other => ({ other, gap: (other.along - actor.along) * actor.direction }))
        .filter(candidate => candidate.gap > 0).sort((a, b) => a.gap - b.gap)[0];
      let limit = actor.desiredSpeed;
      if (leader) {
        const gap = leader.gap - (actor.definition.length + leader.other.definition.length) / 2 - 3;
        // Comfortable headway with a physical braking-distance cap.
        limit = Math.min(limit, Math.max(0, gap / 1.8), Math.sqrt(Math.max(0, 2 * 1.9 * gap)));
      }
      const stopLine = actor.direction === 1 ? -15.5 : 17.5;
      const stopGap = (stopLine - actor.along) * actor.direction - actor.definition.length / 2 - 0.7;
      const approaching = stopGap > -0.2;
      const amberCanStop = signal === 'amber' && stopGap > actor.speed * actor.speed / (2 * 2.5) + 1;
      if (signal === 'green') actor.signalStop = false;
      else if (approaching && (signal === 'red' || amberCanStop)) actor.signalStop = true;
      // Once a driver elects to stop on amber, it does not change its mind as the gap closes.
      if (approaching && actor.signalStop) {
        limit = Math.min(limit, Math.sqrt(Math.max(0, 2 * 1.65 * stopGap)), Math.max(0, stopGap * 1.4));
      }
      actor.acceleration = Math.max(-2.5, Math.min(1.05, (limit - actor.speed) * 1.6));
    }
    for (const actor of moving) {
      const stopLine = actor.direction === 1 ? -15.5 : 17.5;
      const stopPosition = stopLine - actor.direction * (actor.definition.length / 2 + 0.7);
      const beforeStop = (stopPosition - actor.along) * actor.direction >= -0.01;
      actor.speed = Math.max(0, actor.speed + actor.acceleration * STEP);
      actor.along += actor.direction * actor.speed * STEP;
      // Enforce the authored stop boundary after integration; a coarse sample must not
      // put a stationary vehicle's front bumper through the painted line.
      if (actor.signalStop && beforeStop && (actor.along - stopPosition) * actor.direction > 0) {
        actor.along = stopPosition; actor.speed = 0; actor.acceleration = -2.5;
      }
      // Traffic re-enters only at the distant corridor boundary, never at the crossing.
      if (actor.along > ROAD_MAX) actor.along = ROAD_MIN;
      else if (actor.along < ROAD_MIN) actor.along = ROAD_MAX;
    }
  }
  return {
    id: DEMO_SCENE_ID, version: '1', label: 'Vestry / West Street', provenance: 'demo', seed,
    cameraId: '511ny-32998', origin: [-74.0114845353, 40.7228914979], duration: 60,
    geometry: {
      roads: [rectangle(-15, ROAD_MIN, 15, ROAD_MAX), rectangle(15, -6, 105, 6)],
      sidewalks: [rectangle(-21, ROAD_MIN, -15.25, ROAD_MAX), rectangle(15.25, ROAD_MIN, 21, -6.25),
        rectangle(15.25, 6.25, 21, ROAD_MAX), rectangle(21, -11, 105, -6.25), rectangle(21, 6.25, 105, 11)],
      crosswalks: [rectangle(-15, -9, 15, -4), rectangle(-15, 6, 15, 11)],
      lanes: Array.from({ length: 6 }, (_, lane) => {
        const direction = lane < 3 ? 1 : -1;
        const across = direction * (4 + (lane % 3) * 4);
        return { id: `lane-${lane}`, width: 4, direction: direction === 1 ? 'north' : 'south',
          points: [streetPoint(across, direction === 1 ? ROAD_MIN : ROAD_MAX), streetPoint(across, direction === 1 ? ROAD_MAX : ROAD_MIN)] };
      }),
      route: Array.from({ length: 81 }, (_, i) => streetPoint(8, ROAD_MIN + i * 5)),
    },
    actors: moving.map(actor => actor.definition), frames,
  };
}

export const demoScenario = createDemoScenario();

export class DemoScenarioProvider implements ScenarioProvider {
  readonly source = 'demo' as const;
  async loadScenario(scenarioId: string, signal?: AbortSignal): Promise<ScenarioBundle> {
    if (signal?.aborted) throw new DOMException('The request was cancelled', 'AbortError');
    if (scenarioId !== DEMO_SCENE_ID) throw new Error(`Unknown demonstration scene: ${scenarioId}`);
    return demoScenario;
  }
}

/** Pure random access: seeking never advances or mutates simulation state. */
export function sampleScenario(scenario: ScenarioBundle, requestedTime: number): SimulationFrame {
  const time = Math.max(0, Math.min(scenario.duration, Number.isFinite(requestedTime) ? requestedTime : 0));
  const frames = scenario.frames;
  if (!frames.length) return { time, actors: [], signal: 'red' };
  let lo = 0, hi = frames.length - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (frames[mid].time <= time) lo = mid; else hi = mid - 1;
  }
  const a = frames[lo], b = frames[Math.min(lo + 1, frames.length - 1)];
  const alpha = b.time === a.time ? 0 : (time - a.time) / (b.time - a.time);
  const nextActors = new Map(b.actors.map(actor => [actor.id, actor]));
  return { time, signal: a.signal, actors: a.actors.map(actor => {
    const next = nextActors.get(actor.id) ?? actor;
    // A boundary re-entry is a discontinuity. Never interpolate across the intersection.
    const discontinuous = Math.hypot(next.x - actor.x, next.y - actor.y) > 50;
    const blend = discontinuous ? 0 : alpha;
    const turn = Math.atan2(Math.sin(next.heading - actor.heading), Math.cos(next.heading - actor.heading));
    return { ...actor, x: actor.x + (next.x - actor.x) * blend, y: actor.y + (next.y - actor.y) * blend,
      heading: actor.heading + turn * blend, speed: actor.speed + (next.speed - actor.speed) * blend };
  }) };
}
