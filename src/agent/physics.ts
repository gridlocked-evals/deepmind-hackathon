import { DEMO_SCENE_ID, demoSignal, sampleScenario, streetPoint } from '../scene/DemoScenarioProvider';
import type { ActorDefinition, ActorState, ScenarioBundle, SimulationFrame, Vec2 } from '../types';
import { SPEED_LIMIT_MPS } from './types';
import type { DriverDecision, DriverObservation } from './types';

export const MAX_PHYSICS_STEP = 0.1;
export const MAX_ACCELERATION = 1.5;
export const COMFORTABLE_BRAKING = 3.5;
const MIN_GAP = 3;

export interface RoutePath {
  points: Vec2[];
  distances: number[];
  length: number;
}

export interface DriverSimulation {
  scenario: ScenarioBundle;
  path: RoutePath;
  ego: ActorDefinition;
  time: number;
  progress: number;
  speed: number;
  braking: boolean;
  stopProgress: number | null;
  signalStop: boolean;
}

export function createPath(points: Vec2[]): RoutePath {
  const clean = points.filter((point, i) => i === 0 || Math.hypot(point[0] - points[i - 1][0], point[1] - points[i - 1][1]) > 0.0001);
  const distances = [0];
  for (let i = 1; i < clean.length; i++) distances.push(distances[i - 1] + Math.hypot(clean[i][0] - clean[i - 1][0], clean[i][1] - clean[i - 1][1]));
  return { points: clean, distances, length: distances[distances.length - 1] };
}

export function routePosition(path: RoutePath, progress: number) {
  let segment = 0;
  const distance = Math.max(0, Math.min(path.length, progress));
  while (segment < path.points.length - 2 && path.distances[segment + 1] <= distance) segment++;
  const a = path.points[segment] ?? [0, 0];
  const b = path.points[segment + 1] ?? a;
  const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
  const ratio = length ? (distance - path.distances[segment]) / length : 0;
  return { x: a[0] + (b[0] - a[0]) * ratio, y: a[1] + (b[1] - a[1]) * ratio,
    heading: Math.atan2(b[1] - a[1], b[0] - a[0]), segment };
}

export function projectOntoRoute(path: RoutePath, point: Vec2) {
  let best = { progress: 0, lateralDistance: Infinity };
  for (let i = 0; i < path.points.length - 1; i++) {
    const a = path.points[i], b = path.points[i + 1];
    const dx = b[0] - a[0], dy = b[1] - a[1], length = Math.hypot(dx, dy);
    const fraction = Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (length * length)));
    const lateralDistance = Math.hypot(point[0] - a[0] - dx * fraction, point[1] - a[1] - dy * fraction);
    if (lateralDistance < best.lateralDistance) best = { progress: path.distances[i] + fraction * length, lateralDistance };
  }
  return best;
}

export function driverEligibility(scenario: ScenarioBundle): string | null {
  if (scenario.motionSource === 'location-preview' || createPath(scenario.geometry.route).length < 1) return 'Plan a street route or open Traffic demo to drive.';
  if (!scenario.actors.some(actor => actor.role === 'ego')) return 'This scene does not have a vehicle to drive.';
  return null;
}

export function createDriverSimulation(scenario: ScenarioBundle): DriverSimulation {
  const ego = scenario.actors.find(actor => actor.role === 'ego');
  if (!ego) throw new Error('This scene does not have a vehicle to drive.');
  const initial = sampleScenario(scenario, 0).actors.find(actor => actor.id === ego.id);
  if (!initial) throw new Error('The vehicle has no initial position.');
  const path = createPath(scenario.geometry.route);
  const progress = projectOntoRoute(path, [initial.x, initial.y]).progress;
  // Matches the authored northbound stop line and front-bumper margin in DemoScenarioProvider.
  const stopProgress = scenario.id === DEMO_SCENE_ID
    ? projectOntoRoute(path, streetPoint(8, -15.5)).progress - ego.length / 2 - 0.7 : null;
  return { scenario, path, ego, time: 0, progress, speed: Math.min(SPEED_LIMIT_MPS, initial.speed),
    braking: false, stopProgress, signalStop: false };
}

function nearestLeader(state: DriverSimulation, time = state.time) {
  const sample = sampleScenario(state.scenario, time);
  let best: { gap: number; speed: number; progress: number; length: number } | null = null;
  const heading = routePosition(state.path, state.progress).heading;
  for (const actor of sample.actors) {
    if (actor.id === state.ego.id || Math.cos(actor.heading - heading) < 0.7) continue;
    const definition = state.scenario.actors.find(candidate => candidate.id === actor.id);
    if (!definition || definition.routeId !== state.ego.routeId) continue;
    const projected = projectOntoRoute(state.path, [actor.x, actor.y]);
    if (projected.lateralDistance > 1.5 || projected.progress <= state.progress) continue;
    const gap = projected.progress - state.progress - (state.ego.length + definition.length) / 2;
    if (!best || gap < best.gap) best = { gap, speed: actor.speed, progress: projected.progress, length: definition.length };
  }
  return best;
}

function upcomingTurn(state: DriverSimulation) {
  const here = routePosition(state.path, state.progress);
  const ahead = routePosition(state.path, Math.min(state.path.length, state.progress + Math.max(15, state.speed * 3)));
  return Math.atan2(Math.sin(ahead.heading - here.heading), Math.cos(ahead.heading - here.heading)) * 180 / Math.PI;
}

export function driverObservation(state: DriverSimulation): DriverObservation {
  const leader = nearestLeader(state);
  const stopDistance = state.stopProgress === null ? null : state.stopProgress - state.progress;
  return { scenario_id: state.scenario.id.slice(0, 80), simulation_time: state.time, speed_mps: state.speed,
    speed_limit_mps: SPEED_LIMIT_MPS, remaining_m: Math.max(0, state.path.length - state.progress),
    signal: state.scenario.id === DEMO_SCENE_ID ? demoSignal(state.time) : 'unknown',
    stop_distance_m: stopDistance !== null && stopDistance >= -0.01 ? Math.max(0, stopDistance) : null,
    lead_gap_m: leader ? Math.max(0, Math.min(10_000, leader.gap)) : null,
    lead_speed_mps: leader?.speed ?? null, upcoming_turn_degrees: upcomingTurn(state), route_label: state.scenario.label.slice(0, 160) };
}

/** Gemini chooses desired speed; this deterministic layer follows the route and enforces constraints. */
export function advanceDriver(state: DriverSimulation, decision: DriverDecision, requestedDelta: number): DriverSimulation {
  const delta = Math.min(MAX_PHYSICS_STEP, Math.max(0, requestedDelta));
  if (!delta) return state;
  const observation = driverObservation(state);
  let desired = decision.action === 'stop' ? 0 : Math.max(0, Math.min(SPEED_LIMIT_MPS, decision.target_speed_mps));
  if (decision.action === 'brake') desired = Math.min(desired, state.speed);
  const remaining = state.path.length - state.progress;
  desired = Math.min(desired, Math.sqrt(Math.max(0, 2 * 2 * remaining)), remaining * 1.5);
  // Slow near sharp polyline turns; these are route steering, not model-generated steering commands.
  const turn = Math.abs(observation.upcoming_turn_degrees);
  if (turn > 25) desired = Math.min(desired, Math.max(2.5, 8 * (1 - Math.min(150, turn) / 180)));
  if (observation.lead_gap_m !== null) {
    const room = Math.max(0, observation.lead_gap_m - MIN_GAP);
    desired = Math.min(desired, room / 1.8,
      Math.sqrt((observation.lead_speed_mps ?? 0) ** 2 + 2 * COMFORTABLE_BRAKING * room));
  }
  let signalStop = state.signalStop;
  if (observation.signal === 'green') signalStop = false;
  else if (observation.stop_distance_m !== null && (observation.signal === 'red'
    || (observation.signal === 'amber' && observation.stop_distance_m > state.speed ** 2 / (2 * COMFORTABLE_BRAKING) + 1))) signalStop = true;
  if (signalStop && observation.stop_distance_m !== null) desired = Math.min(desired,
    Math.sqrt(2 * 2 * observation.stop_distance_m), observation.stop_distance_m * 1.5);

  const acceleration = Math.max(-COMFORTABLE_BRAKING, Math.min(MAX_ACCELERATION, (desired - state.speed) * 1.8));
  let speed = Math.max(0, Math.min(SPEED_LIMIT_MPS, state.speed + acceleration * delta));
  let progress = state.progress + (state.speed + speed) * 0.5 * delta;
  let boundary = state.path.length;
  if (signalStop && observation.stop_distance_m !== null && state.stopProgress !== null) boundary = Math.min(boundary, state.stopProgress);
  const nextLeader = nearestLeader(state, state.time + delta);
  if (nextLeader) boundary = Math.min(boundary, nextLeader.progress - (state.ego.length + nextLeader.length) / 2 - MIN_GAP);
  // Collision and end boundaries may override comfortable braking in an emergency.
  if (progress >= boundary) { progress = Math.max(state.progress, boundary); speed = 0; }
  return { ...state, time: state.time + delta, progress, speed,
    signalStop, braking: acceleration < -0.25 || speed < state.speed - 0.01 };
}

export function driverFrame(state: DriverSimulation): SimulationFrame {
  const background = sampleScenario(state.scenario, state.time);
  const position = routePosition(state.path, state.progress);
  const ego: ActorState = { id: state.ego.id, x: position.x, y: position.y, heading: position.heading,
    speed: state.speed, braking: state.braking };
  return { time: state.time, signal: background.signal,
    actors: background.actors.map(actor => actor.id === state.ego.id ? ego : actor) };
}

export function routeComplete(state: DriverSimulation) {
  return state.path.length - state.progress < 0.05 && state.speed < 0.1;
}
