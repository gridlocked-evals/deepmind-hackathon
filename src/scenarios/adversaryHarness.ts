import { vi } from 'vitest';
import { demoScenario } from '../scene/DemoScenarioProvider';
import { runContinuousDriver } from '../agent/continuous';
import type { DriverClock } from '../agent/continuous';
import { createDriverSimulation } from '../agent/physics';
import type { DriverSimulation } from '../agent/physics';
import { MAX_DRIVER_STEPS } from '../agent/types';
import type { DriverDecision, DriverObservation } from '../agent/types';
import { createEvents, withEventActors } from './events';
import { beginDecision, createTrace, recordFrame } from './trace';
import { ADVERSARY_PLAN, adversaryBarrier } from './adversary';
import type { AdversarySettings, AttackDecision } from './adversary';

/** Test support (no tests here): a fake animation clock and the real adversary + continuous-driver loop on it. */
export type CarbonBody = { attack_index: number; state: { simulation_time: number }; previous_attacks: unknown[] };
const WAIT: AttackDecision = { action: 'wait', event_type: null, trigger_seconds: null, parameters: null, durationSeconds: null, reason: 'Fixture waits.' };
const HOLD: DriverDecision = { action: 'stop', target_speed_mps: 0, reason: 'Fixture stops', confidence: 1 };

export /** The real loop: runContinuousDriver on a fake clock, wired like useGeminiDriver (onPublish never writes state).
 * Carbon is a stubbed fetch with `carbonMs` latency; the driver answers `policy(observation)` after `driverMs`. */
async function loop(settings: AdversarySettings, { carbon: carbonPolicy = () => WAIT, carbonMs = 0, driverMs = 200, ms = 20_000, frameMs = 20, policy = () => HOLD }: {
  carbon?: (body: CarbonBody) => AttackDecision; carbonMs?: number; driverMs?: number; ms?: number; frameMs?: number;
  policy?: (observation: DriverObservation) => DriverDecision } = {}) {
  const clock = new FakeClock(frameMs);
  let state: DriverSimulation = { ...createDriverSimulation(withEventActors(demoScenario, ADVERSARY_PLAN)), events: createEvents(ADVERSARY_PLAN, 'event'),
    ...(settings.guardsIgnoreHazards ? { guardsIgnoreHazards: true } : {}) };
  const trace = createTrace(state, `fixture-loop-${settings.mode}`, 'test-fixture');
  const carbonCalls: { body: CarbonBody; at: number }[] = [], driverBodies: string[] = [], appliedStates: DriverSimulation[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
    const body = JSON.parse(init.body as string);
    carbonCalls.push({ body, at: clock.now() });
    await new Promise(resolve => clock.timeout(() => resolve(null), carbonMs));
    return new Response(JSON.stringify({ run_id: 'fixture-run-1', attack_index: body.attack_index, model_id: 'gemini-4-carbon', request_id: 'r', max_attacks: 2,
      decision: carbonPolicy(body) }), { status: 200 });
  }));
  const controller = new AbortController();
  const barrier = adversaryBarrier(settings, trace, { runId: 'fixture-run-1', url: '/api/adversary/attack', signal: controller.signal,
    isCurrent: () => true, read: () => state, write: next => { state = next; recordFrame(trace, next); } });
  const done = runContinuousDriver({ initial: state, signal: controller.signal, clock, barrier, readState: () => state,
    onRequest: (step, observation, current) => { beginDecision(trace, current, step, observation); },
    requestDecision: (step, observation) => {
      driverBodies.push(JSON.stringify({ run_id: 'fixture-run-1', step, observation }));
      return new Promise(resolve => clock.timeout(() => resolve({ run_id: 'r', step, model_id: 'gemini-decision-1', request_id: `q${step}`,
        max_steps: MAX_DRIVER_STEPS, decision: policy(observation) }), driverMs));
    },
    onDecision: (response, _observation, current) => {
      appliedStates.push(current);
      Object.assign(trace.steps.find(row => row.step === response.step)!, { response, appliedSimulationTime: current.time });
    },
    onFrame: (_, next) => { state = next; recordFrame(trace, next); }, onPublish: () => undefined,
  }).then(completion => completion, error => error);
  await clock.advance(ms);
  controller.abort();
  const completion = await done;
  return { trace, state, completion, carbonCalls, driverBodies, appliedStates, clock };
}

async function flush() { for (let i = 0; i < 8; i++) await Promise.resolve(); }
export class FakeClock implements DriverClock {
  time = 0; id = 0;
  constructor(private frameMs = 20) {}
  frames = new Map<number, (now: number) => void>();
  timers = new Map<number, { at: number; callback: () => void }>();
  now = () => this.time;
  frame = (callback: (now: number) => void) => { const id = ++this.id; this.frames.set(id, callback); return id; };
  cancelFrame = (id: number) => { this.frames.delete(id); };
  timeout = (callback: () => void, milliseconds: number) => { const id = ++this.id; this.timers.set(id, { at: this.time + milliseconds, callback }); return id; };
  clearTimeout = (id: number) => { this.timers.delete(id); };
  async advance(milliseconds: number) {
    const until = this.time + milliseconds;
    while (this.time < until) {
      this.time += this.frameMs;
      for (const [id, timer] of [...this.timers]) if (timer.at <= this.time && this.timers.delete(id)) timer.callback();
      const frames = [...this.frames.values()]; this.frames.clear();
      for (const callback of frames) callback(this.time);
      await flush();
    }
  }
}
