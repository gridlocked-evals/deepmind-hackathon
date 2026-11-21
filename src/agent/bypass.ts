import type { ActorDefinition, ActorState, LaneDefinition, Vec2 } from '../types';
import { createPath, MAX_ACCELERATION, projectOntoRoute, routePosition } from './physics';
import type { DriverSimulation } from './physics';
import { GO_AROUND_SPEED_MPS } from './types';

const MARGIN = .3;
export interface BypassPlan {
  originLaneId: string;
  laneId: string;
  side: 'left' | 'right';
  offset: number;
  start: number;
  shiftEnd: number;
  returnStart: number;
  end: number;
}
export interface BypassObstacle { id: string; gap: number; speed: number; progress: number; length: number }
export type ActorSampler = (time: number) => ActorState[];

const smooth = (value: number) => { const t = Math.max(0, Math.min(1, value)); return t * t * (3 - 2 * t); };
function offsetAt(plan: BypassPlan, progress: number) {
  return plan.offset * (progress <= plan.shiftEnd ? smooth((progress - plan.start) / (plan.shiftEnd - plan.start))
    : progress < plan.returnStart ? 1 : 1 - smooth((progress - plan.returnStart) / (plan.end - plan.returnStart)));
}

/** Lateral motion is tied to route progress, so stopping also stops the lane change. */
export function bypassPose(state: DriverSimulation, progress = state.progress, plan = state.bypass) {
  const center = routePosition(state.path, progress);
  if (!plan) return center;
  const offset = offsetAt(plan, progress);
  const slope = (offsetAt(plan, progress + .01) - offsetAt(plan, progress - .01)) / .02;
  return { ...center, x: center.x - Math.sin(center.heading) * offset, y: center.y + Math.cos(center.heading) * offset,
    heading: center.heading + Math.atan(slope) };
}

function corners(pose: { x: number; y: number; heading: number }, definition: ActorDefinition) {
  return [-1, 1].flatMap(along => [-1, 1].map(across => [
    pose.x + along * (definition.length / 2 + MARGIN) * Math.cos(pose.heading) - across * (definition.width / 2 + MARGIN) * Math.sin(pose.heading),
    pose.y + along * (definition.length / 2 + MARGIN) * Math.sin(pose.heading) + across * (definition.width / 2 + MARGIN) * Math.cos(pose.heading),
  ] as Vec2));
}

function inside(point: Vec2, polygon: Vec2[]) {
  let result = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i], b = polygon[j];
    if ((a[1] > point[1]) !== (b[1] > point[1]) && point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]) result = !result;
  }
  return result;
}

function overlap(pose: { x: number; y: number; heading: number }, ego: ActorDefinition, actor: ActorState, definition: ActorDefinition) {
  return [pose.heading, pose.heading + Math.PI / 2, actor.heading, actor.heading + Math.PI / 2].every(axis => {
    const distance = Math.abs((actor.x - pose.x) * Math.cos(axis) + (actor.y - pose.y) * Math.sin(axis));
    const radius = (heading: number, box: ActorDefinition) =>
      Math.abs(Math.cos(heading - axis)) * (box.length / 2 + MARGIN) + Math.abs(Math.sin(heading - axis)) * (box.width / 2 + MARGIN);
    return distance <= radius(pose.heading, ego) + radius(actor.heading, definition);
  });
}

function laneAt(state: DriverSimulation, lane: LaneDefinition, progress: number) {
  if (lane.points.length < 2 || !Number.isFinite(lane.width) || lane.width < state.ego.width + MARGIN * 2) return null;
  const center = routePosition(state.path, progress), path = createPath(lane.points);
  const projected = projectOntoRoute(path, [center.x, center.y]);
  if (projected.progress < state.ego.length / 2 || path.length - projected.progress < state.ego.length / 2) return null;
  const point = routePosition(path, projected.progress);
  if (Math.cos(point.heading - center.heading) < .995) return null;
  return { offset: -(point.x - center.x) * Math.sin(center.heading) + (point.y - center.y) * Math.cos(center.heading), point };
}

/** Checks the full remaining swept footprint against known lane/road geometry and current/predicted actors.
 * All actor headings and route IDs count, including approaching traffic from behind. */
export function bypassClear(state: DriverSimulation, plan: BypassPlan, sample: ActorSampler) {
  const origin = state.scenario.geometry.lanes.find(lane => lane.id === plan.originLaneId);
  const target = state.scenario.geometry.lanes.find(lane => lane.id === plan.laneId);
  if (!origin || !target || origin.direction !== target.direction || !state.scenario.geometry.roads.length) return false;
  const heading = routePosition(state.path, plan.start).heading;
  const poses: { progress: number; pose: ReturnType<typeof bypassPose> }[] = [];
  for (let progress = state.progress; progress < plan.end; progress += 1) poses.push({ progress, pose: bypassPose(state, progress, plan) });
  poses.push({ progress: plan.end, pose: bypassPose(state, plan.end, plan) });
  const definitions = new Map(state.scenario.actors.map(actor => [actor.id, actor]));
  const currentActors = sample(state.time);
  for (const { progress, pose } of poses) {
    const center = routePosition(state.path, progress);
    if (Math.cos(center.heading - heading) < .995) return false;
    const original = laneAt(state, origin, progress), adjacent = laneAt(state, target, progress);
    if (!original || !adjacent || Math.abs(original.offset) > .25 || Math.abs(adjacent.offset - plan.offset) > .25) return false;
    const left = Math.min(original.offset - origin.width / 2, adjacent.offset - target.width / 2);
    const right = Math.max(original.offset + origin.width / 2, adjacent.offset + target.width / 2);
    for (const corner of corners(pose, state.ego)) {
      const lateral = -(corner[0] - center.x) * Math.sin(center.heading) + (corner[1] - center.y) * Math.cos(center.heading);
      if (lateral < left || lateral > right || !state.scenario.geometry.roads.some(road => inside(corner, road))) return false;
    }
    const distance = Math.max(0, progress - state.progress), speed = Math.min(GO_AROUND_SPEED_MPS, state.speed);
    const accelerationDistance = (GO_AROUND_SPEED_MPS ** 2 - speed ** 2) / (2 * MAX_ACCELERATION);
    const eta = distance <= accelerationDistance ? (Math.sqrt(speed ** 2 + 2 * MAX_ACCELERATION * distance) - speed) / MAX_ACCELERATION
      : (GO_AROUND_SPEED_MPS - speed) / MAX_ACCELERATION + (distance - accelerationDistance) / GO_AROUND_SPEED_MPS;
    const predicted = [Math.max(0, eta - 1), eta, eta + 1].flatMap(seconds => [
      ...sample(state.time + seconds),
      // Sparse/stale actor samples still reserve an approaching vehicle's velocity corridor.
      ...currentActors.map(actor => ({ ...actor, x: actor.x + Math.cos(actor.heading) * actor.speed * seconds,
        y: actor.y + Math.sin(actor.heading) * actor.speed * seconds })),
    ]);
    for (const actor of [...currentActors, ...predicted]) {
      if (actor.id === state.ego.id) continue;
      const definition = definitions.get(actor.id);
      if (!definition || overlap(pose, state.ego, actor, definition)) return false;
    }
  }
  return true;
}

export function findBypass(state: DriverSimulation, obstacle: BypassObstacle | null, sample: ActorSampler): BypassPlan | null {
  if (state.bypass || !obstacle || obstacle.speed > .5 || obstacle.gap < 12 || obstacle.gap > 55 || state.speed > 6
    || !state.scenario.geometry.lanes.length) return null;
  const origin = state.scenario.geometry.lanes.find(lane => {
    const at = laneAt(state, lane, state.progress); return at && Math.abs(at.offset) <= .25;
  });
  if (!origin) return null;
  const heading = routePosition(state.path, state.progress).heading;
  let farEdge = obstacle.progress + obstacle.length / 2;
  for (const actor of sample(state.time)) {
    if (actor.id === state.ego.id) continue;
    const definition = state.scenario.actors.find(item => item.id === actor.id);
    if (!definition) return null;
    const projected = projectOntoRoute(state.path, [actor.x, actor.y]);
    const angle = actor.heading - heading;
    const halfWidth = (Math.abs(Math.sin(angle)) * definition.length + Math.abs(Math.cos(angle)) * definition.width + state.ego.width) / 2;
    if (projected.lateralDistance <= halfWidth + MARGIN && projected.progress >= obstacle.progress - 8 && projected.progress <= obstacle.progress + 12) {
      const halfLength = (Math.abs(Math.cos(angle)) * definition.length + Math.abs(Math.sin(angle)) * definition.width) / 2;
      farEdge = Math.max(farEdge, projected.progress + halfLength);
    }
  }
  for (const target of state.scenario.geometry.lanes) {
    if (target.id === origin.id || target.direction !== origin.direction) continue;
    const at = laneAt(state, target, state.progress);
    if (!at || Math.abs(at.offset) < 2 || Math.abs(Math.abs(at.offset) - (origin.width + target.width) / 2) > .3) continue;
    const transition = Math.max(12, Math.abs(at.offset) * 3);
    const plan: BypassPlan = { originLaneId: origin.id, laneId: target.id, side: at.offset > 0 ? 'left' : 'right', offset: at.offset,
      start: state.progress, shiftEnd: state.progress + transition,
      returnStart: farEdge + state.ego.length / 2 + 3, end: farEdge + state.ego.length / 2 + 3 + transition };
    if (plan.shiftEnd > obstacle.progress - obstacle.length / 2 - state.ego.length / 2 - 3
      || plan.end + state.ego.length / 2 >= state.path.length) continue;
    if (bypassClear(state, plan, sample)) return plan;
  }
  return null;
}
