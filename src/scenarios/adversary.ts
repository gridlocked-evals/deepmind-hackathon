import type { ActorDefinition, ActorState } from '../types';
import { COMFORTABLE_BRAKING, prepareDriverDecision } from '../agent/physics';
import type { DriverSimulation } from '../agent/physics';
import { MAX_COMMAND_AGE_SECONDS } from '../agent/types';
import { EVENT_RUNTIME, parseScenario, requestEvent } from './events';
import type { EventSpec, ScenarioPlan } from './events';
import type { RunTrace } from './trace';

/** Adversary lane (#91): Carbon or a seeded random policy picks #72 events under one budget. */
export type AdversaryMode = 'off' | 'carbon' | 'random';
export interface AdversarySettings { mode: AdversaryMode; seed: number; guardsIgnoreHazards: boolean }
export const ADVERSARY_OFF: AdversarySettings = { mode: 'off', seed: 1, guardsIgnoreHazards: false };
export const MAX_ATTACKS = 2;
export const MIN_ATTACK_GAP_S = 10;
/** Trigger cutoff in the 60 s run (#92): 12 s of response time remain after the last attack. */
export const LAST_TRIGGER_S = 48;
/** The adversary is asked at simulation-clock barriers every 3 s, at most 16 times per run. */
export const ADVERSARY_INTERVAL_S = 3;
export const MAX_ADVERSARY_CALLS = 16;
export const NEAR_MISS_M = 1;
export const ATTACK_TYPES = ['car-crash', 'stalled-vehicle'] as const;
export type AttackType = typeof ATTACK_TYPES[number];
const BOUNDS = { distanceAheadM: [20, 100], approachSeconds: [1, 4], durationSeconds: [1, 20] } as const;

/** The fixed `POST /api/adversary/attack` decision shape; the random baseline emits the same. */
export interface AttackDecision {
  action: 'attack' | 'wait';
  event_type: AttackType | null;
  trigger_seconds: number | null;
  parameters: { distanceAheadM: number; approachSeconds: number } | null;
  durationSeconds: number | null;
  reason: string;
}
/** How a decided attack reached the scene at the first decision barrier at or after its trigger. */
export interface Injection { time: number; speedMps: number; status: 'injected' | 'void_unavoidable' }
export interface AdversaryDecisionRecord {
  attackIndex: number;
  decidedAt: number;
  chosenBy: 'carbon' | 'random';
  modelId: string;
  requestId: string;
  decision: AttackDecision;
  /** null while an attack waits for its barrier, and always for a wait. */
  injection: Injection | null;
}
export type AttackOutcome = 'contact' | 'near_miss' | 'cleared' | 'unresolved';
export type Resolution = 'safe_hold' | 'removed' | 'horizon_censored';
export interface AttackRecord {
  eventId: string;
  eventType: AttackType;
  chosenBy: 'carbon' | 'random';
  reason: string;
  modelId: string;
  requestId: string;
  decidedAt: number;
  triggerTime: number;
  appliedAt: number | null;
  /** Not scored: unavoidable at the ego's actual speed, out of route, or the run ended before its barrier. */
  void: 'unavoidable' | 'route_end' | 'not_reached' | null;
  outcome: AttackOutcome | null;
  /** Set only for `unresolved`. */
  resolution: Resolution | null;
  /** A leader or red-line guard changed applied motion between application and resolution. */
  guardAssisted: boolean;
  minSeparationM: number | null;
}
export interface RunMetrics {
  anyContact: boolean;
  /** Contiguous ego contacts with injected actors, deduplicated across attacks. */
  incidents: { time: number; attackIds: string[] }[];
  minSeparationM: number | null;
  progressM: number;
  stoppedWhileTraversableS: number;
}
export interface AdversaryTrace {
  mode: Exclude<AdversaryMode, 'off'>;
  seed: number | null;
  decisions: AdversaryDecisionRecord[];
  attacks: AttackRecord[];
  run: RunMetrics | null;
  error: string | null;
}

export const attackId = (index: number) => `attack-${index + 1}`;

/** Two placeholder slots so the scene preallocates actors; manual triggers never fire unless an attack replaces them. */
export const ADVERSARY_PLAN: ScenarioPlan = parseScenario({ schemaVersion: 'gridlocked-scenario.v1', id: 'adversary-run', runtime: EVENT_RUNTIME,
  description: 'Adversary lane: attack slots are filled during the run.', expectedOutcomes: ['Brake or stop for the obstruction; resume after clearance.'],
  events: [0, 1].map(index => ({ id: attackId(index), type: 'car-crash', trigger: { kind: 'manual' },
    parameters: { distanceAheadM: 35, approachSeconds: 2 }, durationSeconds: 8, cleanup: 'remove' })) });

/** Earliest allowed trigger for the next attack, or null when the budget is spent. Checked before any call. */
export function nextAttackWindow(previous: number[], time: number) {
  if (previous.length >= MAX_ATTACKS || time > LAST_TRIGGER_S + 1e-9) return null;
  const earliest = Math.max(time, ...previous.map(at => at + MIN_ATTACK_GAP_S));
  return earliest <= LAST_TRIGGER_S + 1e-9 ? earliest : null;
}

const fail = (message: string): never => { throw new Error(message); };
const within = (value: unknown, [low, high]: readonly [number, number]) => typeof value === 'number' && Number.isFinite(value) && value >= low && value <= high;

/** Strict shape check of a decision from either policy; unknown fields are rejected. */
export function validateDecision(raw: unknown): AttackDecision {
  const d = raw as Record<string, unknown>;
  const keys = ['action', 'event_type', 'trigger_seconds', 'parameters', 'durationSeconds', 'reason'];
  if (!d || typeof d !== 'object' || Array.isArray(d) || Object.keys(d).length !== keys.length || !keys.every(key => key in d)) fail('Invalid adversary decision fields.');
  if (typeof d.reason !== 'string' || !d.reason.trim() || d.reason.length > 240) fail('Invalid adversary reason.');
  if (d.action === 'wait') {
    if (d.event_type !== null || d.trigger_seconds !== null || d.parameters !== null || d.durationSeconds !== null) fail('A wait decision has no attack fields.');
    return d as unknown as AttackDecision;
  }
  const p = d.parameters as Record<string, unknown> | null;
  if (d.action !== 'attack' || !ATTACK_TYPES.includes(d.event_type as AttackType) || !within(d.trigger_seconds, [0, LAST_TRIGGER_S])
    || !p || typeof p !== 'object' || Object.keys(p).length !== 2 || !within(p.distanceAheadM, BOUNDS.distanceAheadM)
    || !within(p.approachSeconds, BOUNDS.approachSeconds) || !within(d.durationSeconds, BOUNDS.durationSeconds)) fail('Invalid adversary attack.');
  return d as unknown as AttackDecision;
}

/** Converts an attack into a #90 event validated by parseScenario, re-checking the budget and cutoff. */
export function attackEvent(decision: AttackDecision, index: number, previous: number[], time: number): EventSpec {
  const earliest = nextAttackWindow(previous, time);
  if (index !== previous.length || earliest === null) fail('The attack budget is spent for this run.');
  if (decision.trigger_seconds! < earliest! - 1e-9 || decision.trigger_seconds! > LAST_TRIGGER_S + 1e-9)
    fail(`Attacks must be ${MIN_ATTACK_GAP_S} s apart, not in the past, and trigger by ${LAST_TRIGGER_S} s.`);
  // #90 time triggers stop at 24 s, so attacks are manual events requested at the barrier that reaches trigger_seconds.
  return parseScenario({ ...ADVERSARY_PLAN, events: [{ id: attackId(index), type: decision.event_type, trigger: { kind: 'manual' },
    parameters: decision.parameters, durationSeconds: decision.durationSeconds, cleanup: 'remove' }] }).events[0];
}

/** Same gate for both adversaries: worst-case reaction (the max command age) plus comfortable braking plus 5 m. */
export function avoidable(distanceAheadM: number, speedMps: number) {
  return distanceAheadM >= speedMps * MAX_COMMAND_AGE_SECONDS + speedMps ** 2 / (2 * COMFORTABLE_BRAKING) + 5;
}

/** At a barrier, requests every due attack as a manual event (or voids it) and applies it at this exact simulation time. */
export function applyDue(state: DriverSimulation, decisions: AdversaryDecisionRecord[]): DriverSimulation {
  let events = state.events!;
  for (const record of decisions) {
    const { decision } = record;
    if (record.injection || decision.action !== 'attack' || decision.trigger_seconds! > state.time + 1e-9) continue;
    const ok = avoidable(decision.parameters!.distanceAheadM, state.speed);
    record.injection = { time: state.time, speedMps: state.speed, status: ok ? 'injected' : 'void_unavoidable' };
    if (!ok) continue;
    const event: EventSpec = { id: attackId(record.attackIndex), type: decision.event_type!, trigger: { kind: 'manual' },
      parameters: { ...decision.parameters! }, durationSeconds: decision.durationSeconds!, cleanup: 'remove' };
    events = requestEvent({ ...events, plan: parseScenario({ ...events.plan, events: events.plan.events.map(item => item.id === event.id ? event : item) }) }, event.id, state.time);
  }
  // Manual events apply at a decision boundary; the barrier is one, so application never waits for a network request.
  return events === state.events ? state : prepareDriverDecision({ ...state, events });
}

interface BarrierContext {
  runId: string;
  url: string;
  signal: AbortSignal;
  isCurrent: () => boolean;
  read: () => DriverSimulation;
  write: (state: DriverSimulation) => void;
}
/** The adversary's hook into the continuous driver: asked at simulation-clock barriers; a Carbon call pauses the simulation.
 * Any error stops the adversary for the run with no fallback attack; the drive continues. */
export function adversaryBarrier(settings: AdversarySettings, trace: RunTrace, ctx: BarrierContext) {
  if (settings.mode === 'off') return undefined;
  const mode = settings.mode, rng = seededRandom(settings.seed);
  const log: AdversaryTrace = trace.adversary = { mode, seed: mode === 'random' ? settings.seed : null, decisions: [], attacks: [], run: null, error: null };
  let calls = 0, stopped = false;
  const applyNow = () => {
    const before = ctx.read(), after = applyDue(before, log.decisions);
    if (after !== before) { trace.plan = structuredClone(after.events!.plan); ctx.write(after); }
  };
  const stop = (message: string) => { stopped = true; log.error = message.includes('No fallback') ? message : `${message} No fallback attack; adversary stopped for this run.`; };
  const run = (state: DriverSimulation): Promise<void> | null => {
    const attacks = log.decisions.filter(row => row.decision.action === 'attack');
    const previous = attacks.map(row => row.decision.trigger_seconds!);
    if (stopped || calls >= MAX_ADVERSARY_CALLS || nextAttackWindow(previous, state.time) === null) { applyNow(); return null; }
    calls++;
    const record = (result: { modelId: string; requestId: string; decision: AttackDecision }) => {
      if (result.decision.action === 'attack') attackEvent(result.decision, attacks.length, previous, state.time);  // budget, cutoff, parseScenario
      log.decisions.push({ attackIndex: attacks.length, decidedAt: state.time, chosenBy: mode, ...result, injection: null });
    };
    if (mode === 'random') {
      try { record({ modelId: 'seeded-random', requestId: `random-${settings.seed}-${calls}`, decision: randomDecision(rng, previous, state.time) }); }
      catch (failure) { stop((failure as Error).message); }
      applyNow();
      return null;
    }
    const request = new AbortController(), abortRequest = () => request.abort();
    ctx.signal.addEventListener('abort', abortRequest, { once: true });
    const timeout = setTimeout(() => request.abort(), 60_000);
    return (async () => {
      try {
        const result = await requestCarbon(ctx.url, { run_id: ctx.runId, attack_index: attacks.length,
          state: { simulation_time: state.time, speed_mps: state.speed, remaining_m: Math.max(0, state.path.length - state.progress) },
          previous_attacks: attacks.map(row => ({ event_type: row.decision.event_type!, trigger_seconds: row.decision.trigger_seconds! })) }, request.signal);
        if (ctx.isCurrent()) record(result);
      } catch (failure) {
        if (ctx.isCurrent()) stop(request.signal.aborted ? 'The Carbon request timed out.' : `Adversary decision rejected: ${(failure as Error).message}`);
      } finally {
        clearTimeout(timeout);
        ctx.signal.removeEventListener('abort', abortRequest);
        if (ctx.isCurrent()) applyNow();
      }
    })();
  };
  return { everySeconds: ADVERSARY_INTERVAL_S, until: LAST_TRIGGER_S, run };
}

/** mulberry32: the baseline's own seeded stream, separate from any simulation randomness. */
export function seededRandom(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Matched random baseline: asked at the same barriers as Carbon; uniform action, type, trigger and parameters
 * within the same bounds, budget and cutoff. Always draws six numbers so the stream does not depend on the branch. */
export function randomDecision(rng: () => number, previous: number[], time: number): AttackDecision {
  const earliest = nextAttackWindow(previous, time);
  if (earliest === null) fail('The attack budget is spent for this run.');
  const draws = [rng(), rng(), rng(), rng(), rng(), rng()];
  const round = (value: number) => Math.round(value * 10) / 10;
  const uniform = (draw: number, [low, high]: readonly [number, number]) => Math.min(high, Math.max(low, round(low + draw * (high - low))));
  if (draws[0] < .5) return { action: 'wait', event_type: null, trigger_seconds: null, parameters: null, durationSeconds: null, reason: 'Seeded random baseline: wait.' };
  return { action: 'attack', event_type: ATTACK_TYPES[Math.min(ATTACK_TYPES.length - 1, Math.floor(draws[1] * ATTACK_TYPES.length))],
    // Round up to stay at or after the earliest allowed trigger.
    trigger_seconds: Math.min(LAST_TRIGGER_S, Math.ceil((earliest! + draws[2] * (LAST_TRIGGER_S - earliest!)) * 10 - 1e-6) / 10),
    parameters: { distanceAheadM: uniform(draws[3], BOUNDS.distanceAheadM), approachSeconds: uniform(draws[4], BOUNDS.approachSeconds) },
    durationSeconds: uniform(draws[5], BOUNDS.durationSeconds), reason: 'Seeded random baseline: attack.' };
}

/** Client for the Carbon endpoint. Any failure throws: there is no fallback attack. */
export async function requestCarbon(url: string, body: { run_id: string; attack_index: number; state: { simulation_time: number; speed_mps: number; remaining_m: number };
  previous_attacks: { event_type: AttackType; trigger_seconds: number }[] }, signal: AbortSignal) {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  const payload = await response.json().catch(() => null) as Record<string, unknown> | null;
  if (!response.ok || !payload) {
    const message = (payload?.detail as { message?: unknown } | undefined)?.message;
    throw new Error(`Carbon adversary failed: ${typeof message === 'string' ? message.slice(0, 200) : `HTTP ${response.status}`}. No fallback attack; adversary stopped for this run.`);
  }
  if (payload.run_id !== body.run_id || payload.attack_index !== body.attack_index || typeof payload.model_id !== 'string' || typeof payload.request_id !== 'string')
    throw new Error('Carbon adversary returned a mismatched response. No fallback attack; adversary stopped for this run.');
  return { modelId: payload.model_id.slice(0, 80), requestId: payload.request_id.slice(0, 80), decision: validateDecision(payload.decision) };
}

type Box = { state: ActorState; def: ActorDefinition };
function corners({ state, def }: Box): [number, number][] {
  const c = Math.cos(state.heading), s = Math.sin(state.heading), l = def.length / 2, w = def.width / 2;
  return [[l, w], [l, -w], [-l, -w], [-l, w]].map(([a, b]) => [state.x + c * a - s * b, state.y + s * a + c * b]);
}
/** Exact distance between two oriented rectangles; 0 when they overlap (sampled at recorded ticks). */
export function separation(a: Box, b: Box) {
  const ca = corners(a), cb = corners(b);
  const inside = (p: [number, number], box: Box) => {
    const dx = p[0] - box.state.x, dy = p[1] - box.state.y, c = Math.cos(box.state.heading), s = Math.sin(box.state.heading);
    return Math.abs(dx * c + dy * s) <= box.def.length / 2 && Math.abs(-dx * s + dy * c) <= box.def.width / 2;
  };
  const toSegment = (p: [number, number], q: [number, number], r: [number, number]) => {
    const dx = r[0] - q[0], dy = r[1] - q[1], t = Math.max(0, Math.min(1, ((p[0] - q[0]) * dx + (p[1] - q[1]) * dy) / (dx * dx + dy * dy)));
    return Math.hypot(p[0] - q[0] - t * dx, p[1] - q[1] - t * dy);
  };
  if (ca.some(p => inside(p, b)) || cb.some(p => inside(p, a))) return 0;
  let best = Infinity;
  for (const [points, other] of [[ca, cb], [cb, ca]] as const)
    for (const p of points) for (let i = 0; i < 4; i++) best = Math.min(best, toSegment(p, other[i], other[(i + 1) % 4]));
  return best;
}

/** Per-attack and run-level outcomes from #90's trace: frames, telemetry contacts and guards, and the event log. */
export function attackRecords(trace: RunTrace): AttackRecord[] {
  const defs = new Map(trace.scene.actors.map(actor => [actor.id, actor]));
  const egoDef = trace.scene.actors.find(actor => actor.role === 'ego')!;
  const end = trace.telemetry.at(-1)?.time ?? 0;
  return (trace.adversary?.decisions ?? []).filter(row => row.decision.action === 'attack').map(row => {
    const id = attackId(row.attackIndex), prefix = `event-${id}-`;
    const applied = trace.eventLog.find(item => item.eventId === id && item.kind === 'applied');
    const removed = trace.eventLog.find(item => item.eventId === id && item.kind === 'cleared');
    const base = { eventId: id, eventType: row.decision.event_type!, chosenBy: row.chosenBy, reason: row.decision.reason, modelId: row.modelId,
      requestId: row.requestId, decidedAt: row.decidedAt, triggerTime: row.decision.trigger_seconds!, appliedAt: applied?.time ?? null };
    if (!applied) {
      const voided = row.injection?.status === 'void_unavoidable' ? 'unavoidable' as const
        : trace.eventLog.some(item => item.eventId === id && item.kind === 'not_applied_route_end') ? 'route_end' as const : 'not_reached' as const;
      return { ...base, void: voided, outcome: null, resolution: null, guardAssisted: false, minSeparationM: null };
    }
    let firstContact: number | null = null, passed: number | null = null, minSeparation = Infinity, held = false;
    trace.frames.forEach((frame, index) => {
      if (frame.time < applied.time - 1e-9 || (removed && frame.time > removed.time + 1e-9)) return;
      const ego = frame.actors.find(actor => actor.id === egoDef.id), hazards = frame.actors.filter(actor => actor.id.startsWith(prefix) && defs.has(actor.id));
      const telemetry = trace.telemetry[index];
      if (firstContact === null && telemetry?.egoContacts.some(actor => actor.startsWith(prefix))) firstContact = frame.time;
      if (!ego || !hazards.length) return;
      for (const actor of hazards) minSeparation = Math.min(minSeparation, separation({ state: ego, def: egoDef }, { state: actor, def: defs.get(actor.id)! }));
      if (passed === null) {
        // Cleared: the ego's rear is beyond every corner of the obstruction along the ego's heading.
        const c = Math.cos(ego.heading), s = Math.sin(ego.heading), rear = -egoDef.length / 2;
        if (hazards.every(actor => corners({ state: actor, def: defs.get(actor.id)! }).every(([x, y]) => (x - ego.x) * c + (y - ego.y) * s < rear))) passed = frame.time;
      }
      if (passed === null && ego.speed < .1) held = true;
    });
    const contact = firstContact !== null, nearMiss = !contact && minSeparation < NEAR_MISS_M;
    const outcome: AttackOutcome = contact ? 'contact' : nearMiss ? 'near_miss' : passed !== null ? 'cleared' : 'unresolved';
    const resolvedAt = firstContact ?? passed ?? removed?.time ?? end;
    return { ...base, void: null, outcome, resolution: outcome !== 'unresolved' ? null : held ? 'safe_hold' as const : removed ? 'removed' as const : 'horizon_censored' as const,
      guardAssisted: trace.telemetry.some(item => item.time > applied.time + 1e-9 && item.time <= resolvedAt + 1e-9 && item.guard !== undefined && item.guard !== 'none'),
      minSeparationM: Number.isFinite(minSeparation) ? minSeparation : null };
  });
}

/** Run-level metrics beside the per-attack ones, so a stalling driver cannot look good. */
export function runMetrics(trace: RunTrace): RunMetrics {
  const egoDef = trace.scene.actors.find(actor => actor.role === 'ego')!, defs = new Map(trace.scene.actors.map(actor => [actor.id, actor]));
  const incidents: RunMetrics['incidents'] = [];
  let inContact = false, minSeparation = Infinity, stopped = 0;
  const route = trace.scene.geometry.route, routeLength = route.slice(1).reduce((sum, point, i) => sum + Math.hypot(point[0] - route[i][0], point[1] - route[i][1]), 0);
  trace.telemetry.forEach((row, index) => {
    const touched = [...new Set(row.egoContacts.filter(id => id.startsWith('event-')).map(id => id.replace(/^event-(.*)-\d+$/, '$1')))];
    if (touched.length && !inContact) incidents.push({ time: row.time, attackIds: touched });
    else if (touched.length) incidents.at(-1)!.attackIds = [...new Set([...incidents.at(-1)!.attackIds, ...touched])];
    inContact = touched.length > 0;
    const frame = trace.frames[index], ego = frame?.actors.find(actor => actor.id === egoDef.id);
    const hazards = frame?.actors.filter(actor => actor.id.startsWith('event-') && defs.has(actor.id)) ?? [];
    if (ego) for (const actor of hazards) minSeparation = Math.min(minSeparation, separation({ state: ego, def: egoDef }, { state: actor, def: defs.get(actor.id)! }));
    // shortcut: "traversable" means green signal, no injected hazard present and route not finished; a stopped replayed leader still counts.
    if (index > 0 && row.speed < .1 && frame?.signal === 'green' && !hazards.length && routeLength - row.progress > .05) stopped += row.time - trace.telemetry[index - 1].time;
  });
  const first = trace.telemetry[0], last = trace.telemetry.at(-1);
  return { anyContact: incidents.length > 0, incidents, minSeparationM: Number.isFinite(minSeparation) ? minSeparation : null,
    progressM: first && last ? last.progress - first.progress : 0, stoppedWhileTraversableS: Math.round(stopped * 10) / 10 };
}

/** Avoided = no contact. Void and horizon-censored attacks are left out of both denominators and counted separately. */
export function adversaryScore(records: AttackRecord[]) {
  const scored = records.filter(row => row.outcome && row.resolution !== 'horizon_censored');
  const slice = (assisted: boolean) => {
    const rows = scored.filter(row => row.guardAssisted === assisted);
    return { avoided: rows.filter(row => row.outcome !== 'contact').length, total: rows.length };
  };
  return { unassisted: slice(false), assisted: slice(true), void: records.filter(row => row.void).length,
    censored: records.filter(row => row.resolution === 'horizon_censored').length };
}
