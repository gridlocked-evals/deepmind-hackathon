import type { ActorDefinition, ActorState, ScenarioBundle, SimulationFrame } from '../types';
import { driverFrame, driverObservation, observedEventIds } from '../agent/physics';
import type { DriverSimulation } from '../agent/physics';
import type { DriverTrace } from '../agent/trace';
import { MAX_DRIVER_STEPS, SECONDS_PER_DECISION } from '../agent/types';
import type { DriverObservation, DriverResponse } from '../agent/types';
import { EVENT_STEP, parseScenario } from './events';
import type { EventRecord, ScenarioPlan } from './events';

export type Terminal = 'running' | 'route-complete' | 'decision-limit' | 'interrupted' | 'reset' | 'error';
export interface TraceStep {
  step: number;
  observation: DriverObservation;
  observedEventIds: string[];
  response: DriverResponse | null;
  wallLatencyMs: number | null;
  failure: { code: string; requestId: string | null } | null;
}
export interface Telemetry {
  time: number;
  frame: number;
  progress: number;
  speed: number;
  control: DriverSimulation['control'] | null;
  egoContacts: string[];
}
export interface RunTrace {
  schemaVersion: 'gridlocked-driver-trace.v1';
  provenance: 'synthetic-structured-state';
  modelSource: 'live-model' | 'test-fixture';
  modelPolicy: { promptVersion: 'sim-driver-v2'; thinkingLevel: 'low' };
  runId: string;
  createdAt: string;
  mode: 'baseline' | 'event';
  plan: ScenarioPlan;
  scene: ScenarioBundle;
  limits: { maxDecisions: number; secondsPerDecision: number; physicsStepSeconds: number };
  steps: TraceStep[];
  telemetry: Telemetry[];
  frames: SimulationFrame[];
  initialFrame?: SimulationFrame;
  /** The compatible #60 action/guard trace; absent in exports made before integration. */
  driverTrace?: DriverTrace;
  eventLog: EventRecord[];
  appliedEvents: { id: string; appliedAt: number; anchorProgress: number }[];
  terminal: { reason: Terminal; simulationTime: number; message: string | null };
}

/** Oriented rectangle contact at recorded physics ticks; no crash-damage model. */
export function footprintContact(a: ActorState, da: ActorDefinition, b: ActorState, db: ActorDefinition) {
  const axes = [a.heading, a.heading + Math.PI / 2, b.heading, b.heading + Math.PI / 2];
  return axes.every(axis => {
    const separation = Math.abs((b.x - a.x) * Math.cos(axis) + (b.y - a.y) * Math.sin(axis));
    const radius = (state: ActorState, definition: ActorDefinition) =>
      Math.abs(Math.cos(state.heading - axis)) * definition.length / 2 + Math.abs(Math.sin(state.heading - axis)) * definition.width / 2;
    return separation <= radius(a, da) + radius(b, db);
  });
}
export function createTrace(state: DriverSimulation, runId: string, modelSource: RunTrace['modelSource'] = 'live-model'): RunTrace {
  if (!state.events) throw new Error('Missing scenario event state.');
  const trace: RunTrace = { schemaVersion: 'gridlocked-driver-trace.v1', provenance: 'synthetic-structured-state', modelSource,
    modelPolicy: { promptVersion: 'sim-driver-v2', thinkingLevel: 'low' },
    runId, createdAt: new Date().toISOString(), mode: state.events.mode, plan: structuredClone(state.events.plan),
    scene: structuredClone({ ...state.scenario, frames: [] }),
    limits: { maxDecisions: MAX_DRIVER_STEPS, secondsPerDecision: SECONDS_PER_DECISION, physicsStepSeconds: EVENT_STEP },
    steps: [], telemetry: [], frames: [], eventLog: [], appliedEvents: [], terminal: { reason: 'running', simulationTime: 0, message: null } };
  recordFrame(trace, state);
  trace.initialFrame = structuredClone(trace.frames[0]);
  return trace;
}
export function beginDecision(trace: RunTrace, state: DriverSimulation, step: number) {
  const row: TraceStep = { step, observation: driverObservation(state), observedEventIds: observedEventIds(state), response: null, wallLatencyMs: null, failure: null };
  trace.steps.push(row);
  return row;
}
export function recordFrame(trace: RunTrace, state: DriverSimulation) {
  const frame = driverFrame(state), ego = frame.actors.find(actor => actor.id === state.ego.id)!;
  const egoContacts = frame.actors.filter(actor => {
    const definition = state.scenario.actors.find(item => item.id === actor.id);
    return actor.id !== ego.id && definition && footprintContact(ego, state.ego, actor, definition);
  }).map(actor => actor.id);
  const row = { time: state.time, frame: Math.round(state.time / EVENT_STEP), progress: state.progress, speed: state.speed,
    control: state.control ?? null, egoContacts };
  if (trace.frames.at(-1)?.time === state.time) { trace.frames.pop(); trace.telemetry.pop(); }
  trace.frames.push(structuredClone(frame)); trace.telemetry.push(structuredClone(row));
  trace.eventLog = structuredClone(state.events?.log ?? []);
  trace.appliedEvents = state.events?.applied.map(event => ({ id: event.spec.id, appliedAt: event.appliedAt, anchorProgress: event.anchorProgress })) ?? [];
}
export function finishTrace(trace: RunTrace, state: DriverSimulation, reason: Terminal, message: string | null = null) {
  recordFrame(trace, state);
  trace.terminal = { reason, simulationTime: state.time, message };
}
export function summarizeTrace(trace: RunTrace) {
  const first = trace.telemetry[0], last = trace.telemetry.at(-1);
  const events = trace.appliedEvents.map(event => {
    const visible = trace.steps.find(step => step.observedEventIds.includes(event.id));
    const response = trace.steps.find(step => step.observedEventIds.includes(event.id) && step.response
      && (step.response.decision.action === 'stop' || step.response.decision.action === 'brake'
        || step.response.decision.target_speed_mps < step.observation.speed_mps - .1));
    const cleared = trace.eventLog.find(row => row.eventId === event.id && row.kind === 'cleared');
    const stopped = trace.telemetry.find(row => row.time >= event.appliedAt && (!cleared || row.time <= cleared.time) && row.speed < .1);
    const resumed = cleared && trace.telemetry.find(row => row.time > cleared.time && row.speed > 1);
    return { id: event.id, appliedAt: event.appliedAt, firstModelVisibleTime: visible?.observation.simulation_time ?? null,
      modelReactionTime: response?.observation.simulation_time ?? null,
      responseLatencySimulationSeconds: visible && response ? response.observation.simulation_time - visible.observation.simulation_time : null,
      responseLatencyFrames: visible && response ? Math.round((response.observation.simulation_time - visible.observation.simulation_time) / EVENT_STEP) : null,
      stoppedAt: stopped?.time ?? null, clearedAt: cleared?.time ?? null, resumedAt: resumed?.time ?? null };
  });
  return { events, progressM: first && last ? last.progress - first.progress : 0,
    controllerInterventionFrames: trace.telemetry.filter(row => row.control?.interventions.length).length,
    egoContactFrames: trace.telemetry.filter(row => row.egoContacts.length).length,
    injectedHazardContactFrames: trace.telemetry.filter(row => row.egoContacts.some(id => id.startsWith('event-'))).length,
    replayedTrafficContactFrames: trace.telemetry.filter(row => row.egoContacts.some(id => !id.startsWith('event-'))).length,
    scriptedCrashCount: trace.eventLog.filter(row => row.kind === 'scripted_collision').length,
    nearMiss: 'unknown', rerouting: 'unsupported', terminal: trace.terminal.reason,
    notice: 'Synthetic state; sampled rectangle contacts. Stops may include controller interventions. Model reaction is a changed proposal, not online learning.' };
}
export function compareTraces(baseline: RunTrace, event: RunTrace) {
  if (baseline.mode !== 'baseline' || event.mode !== 'event' || baseline.scene.id !== event.scene.id
    || baseline.scene.version !== event.scene.version || baseline.scene.seed !== event.scene.seed
    || baseline.modelSource !== event.modelSource || JSON.stringify(baseline.modelPolicy) !== JSON.stringify(event.modelPolicy) || JSON.stringify(baseline.plan) !== JSON.stringify(event.plan)
    || JSON.stringify(baseline.initialFrame ?? baseline.frames[0]) !== JSON.stringify(event.initialFrame ?? event.frames[0])) return null;
  return { baseline: summarizeTrace(baseline), event: summarizeTrace(event),
    note: 'Same initial scene and event plan; live model decisions may differ. This pair is descriptive, not a causal safety score.' };
}
export function downloadJson(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Only reconstruct allowed scene fields. Imported traces never execute or call a model. */
export function replayScene(raw: unknown): ScenarioBundle {
  const value = raw as Partial<RunTrace> | null;
  if (!value || value.schemaVersion !== 'gridlocked-driver-trace.v1' || value.provenance !== 'synthetic-structured-state') throw new Error('Unsupported trace.');
  parseScenario(value.plan);
  const scene = value.scene, frames = value.frames;
  const finite = (n: unknown, low: number, high: number) => typeof n === 'number' && Number.isFinite(n) && n >= low && n <= high;
  const point = (p: unknown): p is [number, number] => Array.isArray(p) && p.length === 2 && p.every(n => finite(n, -20000, 20000));
  if (!scene || !Array.isArray(scene.origin) || scene.origin.length !== 2 || !finite(scene.origin[0], -180, 180) || !finite(scene.origin[1], -85, 85)
    || !Array.isArray(scene.actors) || !scene.actors.length || scene.actors.length > 64 || !Array.isArray(frames) || !frames.length || frames.length > 400) throw new Error('Invalid replay scene.');
  if (!scene.geometry || !['roads', 'sidewalks', 'crosswalks', 'route', 'lanes'].every(key => Array.isArray(scene.geometry[key as keyof typeof scene.geometry]))) throw new Error('Invalid replay geometry.');
  const paths = [...scene.geometry.roads, ...scene.geometry.sidewalks, ...scene.geometry.crosswalks, scene.geometry.route, ...scene.geometry.lanes.map(lane => lane.points)];
  if (paths.length > 100 || paths.some(path => !Array.isArray(path) || path.length > 5000 || !path.every(point)) || scene.geometry.route.length < 2) throw new Error('Invalid replay coordinates.');
  const safeText = (text: unknown, max: number): text is string => typeof text === 'string' && text.length > 0 && text.length <= max;
  for (const actor of scene.actors) {
    if (!safeText(actor.id, 100) || !safeText(actor.label, 160) || !safeText(actor.routeId, 100)
      || !['car','van','bus'].includes(actor.kind) || !['ego','traffic'].includes(actor.role)
      || !finite(actor.length, .5, 20) || !finite(actor.width, .5, 5) || !finite(actor.height, .5, 5)
      || typeof actor.color !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(actor.color)) throw new Error('Invalid replay actor.');
  }
  const ids = new Set(scene.actors.map(actor => actor.id));
  if (ids.size !== scene.actors.length || scene.actors.filter(actor => actor.role === 'ego').length !== 1) throw new Error('Invalid replay identities.');
  for (const [index, frame] of frames.entries()) {
    if (!finite(frame.time, 0, 36.001) || (index && frame.time <= frames[index - 1].time) || !['green','red','amber'].includes(frame.signal)
      || !Array.isArray(frame.actors) || frame.actors.length > 64 || new Set(frame.actors.map(actor => actor.id)).size !== frame.actors.length) throw new Error('Invalid replay frame.');
    for (const actor of frame.actors) if (!ids.has(actor.id) || !point([actor.x, actor.y]) || !finite(actor.heading, -100, 100)
      || !finite(actor.speed, 0, 50) || typeof actor.braking !== 'boolean' || (actor.disabled !== undefined && typeof actor.disabled !== 'boolean')) throw new Error('Invalid replay state.');
  }
  if (!safeText(scene.id, 80) || !safeText(scene.label, 160) || !safeText(scene.version, 1000) || !finite(scene.seed, 0, 2 ** 32 - 1)) throw new Error('Invalid replay identity.');
  return { id: scene.id, version: `replay-${Date.now()}`, label: `${scene.label} · recorded replay`, provenance: 'demo', seed: scene.seed,
    cameraId: '', origin: scene.origin, duration: frames.at(-1)!.time, playbackMode: 'once', motionSource: 'event-replay',
    geometryKind: scene.geometryKind === 'street-route' ? 'street-route' : 'authored', geometry: scene.geometry,
    actors: scene.actors, frames };
}
