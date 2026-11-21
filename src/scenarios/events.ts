import type { ActorDefinition, ActorState, ScenarioBundle } from '../types';

export const EVENT_RUNTIME = 'gridlocked-structured-v1';
export const EVENT_STEP = 0.1;
export type Trigger = { kind: 'manual' } | { kind: 'time'; seconds: number } | { kind: 'frame'; frame: number };
export interface EventSpec {
  id: string;
  type: string;
  trigger: Trigger;
  parameters: { distanceAheadM: number; approachSeconds: number };
  durationSeconds: number;
  cleanup: 'remove';
}
export interface ScenarioPlan {
  schemaVersion: 'gridlocked-scenario.v1';
  id: string;
  description: string;
  runtime: typeof EVENT_RUNTIME;
  events: EventSpec[];
  expectedOutcomes: string[];
}
export interface AppliedEvent {
  spec: EventSpec;
  requestedAt: number;
  appliedAt: number;
  appliedFrame: number;
  anchorProgress: number;
}
export interface EventRecord {
  eventId: string;
  kind: 'requested' | 'applied' | 'scripted_collision' | 'disabled' | 'cleared' | 'not_applied_route_end';
  time: number;
  frame: number;
}
export interface EventState {
  plan: ScenarioPlan;
  mode: 'baseline' | 'event';
  pending: Record<string, number>;
  applied: AppliedEvent[];
  log: EventRecord[];
}
type Pose = { x: number; y: number; heading: number };
interface EventDefinition {
  label: string;
  actorCount: number;
  contact: boolean;
  sample: (age: number, event: AppliedEvent, pose: Pose) => ActorState[];
}
const registry = new Map<string, EventDefinition>();
export function registerEvent(type: string, definition: EventDefinition) {
  if (!/^[a-z][a-z0-9-]{1,40}$/.test(type) || registry.has(type)
    || !Number.isInteger(definition.actorCount) || definition.actorCount < 1 || definition.actorCount > 2) {
    throw new Error('Invalid or duplicate event registration.');
  }
  registry.set(type, definition);
}
export const eventActorId = (id: string, index: number) => `event-${id}-${index}`;
function actor(event: AppliedEvent, index: number, pose: Pose, along: number, across: number, heading: number, speed: number, disabled: boolean): ActorState {
  return { id: eventActorId(event.spec.id, index), x: pose.x + Math.cos(pose.heading) * along - Math.sin(pose.heading) * across,
    y: pose.y + Math.sin(pose.heading) * along + Math.cos(pose.heading) * across,
    heading: pose.heading + heading, speed, braking: disabled, disabled };
}
registerEvent('car-crash', {
  label: 'Car crash ahead', actorCount: 2, contact: true,
  sample(age, event, pose) {
    const duration = event.spec.parameters.approachSeconds, fraction = Math.min(1, age / duration);
    return [actor(event, 0, pose, -4 * (1 - fraction), 0, fraction * .22, fraction < 1 ? 4 / duration : 0, fraction === 1),
      actor(event, 1, pose, 2, 8 * (1 - fraction), -Math.PI / 2, fraction < 1 ? 8 / duration : 0, fraction === 1)];
  },
});
registerEvent('stalled-vehicle', {
  label: 'Stalled vehicle', actorCount: 1, contact: false,
  sample(age, event, pose) {
    const duration = event.spec.parameters.approachSeconds, fraction = Math.min(1, age / duration);
    return [actor(event, 0, pose, -4 * (1 - fraction) ** 2, 0, 0, fraction < 1 ? 8 * (1 - fraction) / duration : 0, fraction === 1)];
  },
});
export const eventTypes = () => [...registry].map(([type, entry]) => ({ type, label: entry.label }));

function object(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => !keys.includes(key))) throw new Error('Unsupported scenario fields.');
  return value as Record<string, unknown>;
}
function text(value: unknown, max: number) {
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw new Error('Invalid scenario text.');
  return value;
}
function number(value: unknown, min: number, max: number) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max) throw new Error(`Expected a number between ${min} and ${max}.`);
  return value;
}
export function parseScenario(value: unknown): ScenarioPlan {
  const plan = object(value, ['schemaVersion', 'id', 'description', 'runtime', 'events', 'expectedOutcomes']);
  if (plan.schemaVersion !== 'gridlocked-scenario.v1' || plan.runtime !== EVENT_RUNTIME) throw new Error('Unsupported scenario version or runtime.');
  const id = text(plan.id, 50);
  if (!/^[a-zA-Z0-9_-]+$/.test(id) || !Array.isArray(plan.events) || !plan.events.length || plan.events.length > 4) throw new Error('Use a valid ID and one to four events.');
  const events = plan.events.map(raw => {
    const event = object(raw, ['id', 'type', 'trigger', 'parameters', 'durationSeconds', 'cleanup']);
    const eventId = text(event.id, 40), type = text(event.type, 40);
    if (!/^[a-zA-Z0-9_-]+$/.test(eventId) || !registry.has(type) || event.cleanup !== 'remove') throw new Error('Unsupported event, ID or cleanup behavior.');
    const trigger = object(event.trigger, ['kind', 'seconds', 'frame']);
    let validated: Trigger;
    if (trigger.kind === 'manual' && Object.keys(trigger).length === 1) validated = { kind: 'manual' };
    else if (trigger.kind === 'time' && Object.keys(trigger).length === 2 && !('frame' in trigger)) validated = { kind: 'time', seconds: number(trigger.seconds, 0, 24) };
    else if (trigger.kind === 'frame' && Object.keys(trigger).length === 2 && !('seconds' in trigger)) {
      const frame = number(trigger.frame, 0, 240);
      if (!Number.isInteger(frame)) throw new Error('Trigger frame must be an integer.');
      validated = { kind: 'frame', frame };
    } else throw new Error('Invalid trigger.');
    const params = object(event.parameters, ['distanceAheadM', 'approachSeconds']);
    return { id: eventId, type, trigger: validated, parameters: { distanceAheadM: number(params.distanceAheadM, 20, 100),
      approachSeconds: number(params.approachSeconds, 1, 4) }, durationSeconds: number(event.durationSeconds, 1, 20), cleanup: 'remove' as const };
  });
  if (new Set(events.map(event => event.id)).size !== events.length) throw new Error('Event IDs must be unique.');
  if (!Array.isArray(plan.expectedOutcomes) || plan.expectedOutcomes.length > 8) throw new Error('Invalid expected outcomes.');
  return { schemaVersion: 'gridlocked-scenario.v1', id, runtime: EVENT_RUNTIME, description: text(plan.description, 500),
    events, expectedOutcomes: plan.expectedOutcomes.map(value => text(value, 160)) };
}
export function preset(type = 'car-crash'): ScenarioPlan {
  return parseScenario({ schemaVersion: 'gridlocked-scenario.v1', id: `${type}-demo`, runtime: EVENT_RUNTIME,
    description: registry.get(type)?.label, expectedOutcomes: ['Brake or stop for the obstruction; resume after clearance.'],
    events: [{ id: 'hazard-1', type, trigger: { kind: 'time', seconds: 3 }, parameters: { distanceAheadM: 35, approachSeconds: 2 }, durationSeconds: 8, cleanup: 'remove' }] });
}
export function withEventActors(scenario: ScenarioBundle, plan: ScenarioPlan): ScenarioBundle {
  const base = scenario.actors.filter(actor => !actor.id.startsWith('event-'));
  const actors: ActorDefinition[] = plan.events.flatMap(event => Array.from({ length: registry.get(event.type)!.actorCount }, (_, index) => ({
    id: eventActorId(event.id, index), kind: 'car' as const, label: index ? 'Crossing traffic car' : 'Traffic car ahead', role: 'traffic' as const,
    length: 4.55, width: 1.85, height: 1.55, color: index ? '#e4a638' : '#e7ecec', routeId: 'event-actor',
  })));
  if (base.length + actors.length > 64) throw new Error('This scene exceeds the renderer actor limit.');
  return { ...scenario, version: `${scenario.version.split('-events-')[0]}-events-${JSON.stringify(plan.events.map(event => [event.id, event.type]))}`, actors: [...base, ...actors] };
}
export function createEvents(plan: ScenarioPlan, mode: EventState['mode']): EventState {
  return { plan: parseScenario(plan), mode, pending: {}, applied: [], log: [] };
}
export function requestEvent(state: EventState, id: string, time: number): EventState {
  if (state.mode === 'baseline' || !state.plan.events.some(event => event.id === id && event.trigger.kind === 'manual')) throw new Error('This event cannot be manually triggered.');
  if (Object.hasOwn(state.pending, id) || state.applied.some(event => event.spec.id === id)) return state;
  return { ...state, pending: { ...state.pending, [id]: time }, log: [...state.log, { eventId: id, kind: 'requested', time, frame: Math.round(time / EVENT_STEP) }] };
}
export function advanceEvents(state: EventState, time: number, progress: number, routeLength: number, boundary = false): EventState {
  if (state.mode === 'baseline') return state;
  const applied = [...state.applied], log = [...state.log];
  const record = (id: string, kind: EventRecord['kind']) => {
    if (!log.some(row => row.eventId === id && row.kind === kind)) log.push({ eventId: id, kind, time, frame: Math.round(time / EVENT_STEP) });
  };
  for (const event of state.plan.events) {
    if (log.some(row => row.eventId === event.id && row.kind === 'not_applied_route_end')) continue;
    const requestedAt = event.trigger.kind === 'manual'
      ? (Object.hasOwn(state.pending, event.id) ? state.pending[event.id] : undefined)
      : event.trigger.kind === 'time' ? event.trigger.seconds : event.trigger.frame * EVENT_STEP;
    if (requestedAt === undefined || requestedAt > time + 1e-7 || (event.trigger.kind === 'manual' && !boundary)) continue;
    if (!applied.some(item => item.spec.id === event.id)) {
      if (progress + event.parameters.distanceAheadM + 8 >= routeLength) { record(event.id, 'not_applied_route_end'); continue; }
      applied.push({ spec: event, requestedAt, appliedAt: time, appliedFrame: Math.round(time / EVENT_STEP), anchorProgress: progress + event.parameters.distanceAheadM });
      record(event.id, 'applied');
    }
  }
  for (const event of applied) {
    const impact = event.appliedAt + event.spec.parameters.approachSeconds;
    if (time + 1e-7 >= impact) record(event.spec.id, registry.get(event.spec.type)!.contact ? 'scripted_collision' : 'disabled');
    if (time + 1e-7 >= impact + event.spec.durationSeconds) record(event.spec.id, 'cleared');
  }
  return { ...state, applied, log };
}
export function sampleEvents(state: EventState, time: number, poseAt: (progress: number) => Pose): ActorState[] {
  return state.applied.flatMap(event => {
    const age = time - event.appliedAt;
    if (age < 0 || age + 1e-7 >= event.spec.parameters.approachSeconds + event.spec.durationSeconds) return [];
    return registry.get(event.spec.type)!.sample(age, event, poseAt(event.anchorProgress));
  });
}
