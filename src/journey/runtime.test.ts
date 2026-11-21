import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { DriverClock } from '../agent/continuous';
import type { DriverObservation } from '../agent/types';
import type { NavigationPoint } from '../navigation/types';
import { NYC_STOPS, ProgressWatch, randomStop } from './route';
import { runJourney } from './runtime';
import type { JourneyOptions, JourneySnapshot } from './runtime';
import type { RunTrace } from '../scenarios/trace';

const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
class Clock implements DriverClock {
  time = 0; id = 0;
  frames = new Map<number, (time: number) => void>();
  timers = new Map<number, { at: number; run: () => void }>();
  now = () => this.time;
  frame = (run: (time: number) => void) => { const id = ++this.id; this.frames.set(id, run); return id; };
  cancelFrame = (id: number) => { this.frames.delete(id); };
  timeout = (run: () => void, ms: number) => { const id = ++this.id; this.timers.set(id, { at: this.time + ms, run }); return id; };
  clearTimeout = (id: number) => { this.timers.delete(id); };
  async advance(ms: number) {
    await flush();
    for (const end = this.time + ms; this.time < end;) {
      this.time += 50;
      for (const [id, task] of [...this.timers]) if (task.at <= this.time && this.timers.delete(id)) task.run();
      const frames = [...this.frames.values()]; this.frames.clear(); frames.forEach(run => run(this.time));
      await flush();
    }
  }
}
const wait = { action: 'wait', event_type: null, trigger_seconds: null, parameters: null, durationSeconds: null, reason: 'Wait fixture.' };
function carbon(policy: (body: any) => unknown = () => wait) {
  const fetch = vi.fn(async (_url: string, init: RequestInit) => {
    const body = JSON.parse(init.body as string);
    return new Response(JSON.stringify({ run_id: body.run_id, attack_index: body.attack_index,
      model_id: 'gemini-4-carbon', request_id: 'fixture-carbon', decision: policy(body) }));
  });
  vi.stubGlobal('fetch', fetch); return fetch;
}
beforeEach(() => { carbon(); });
afterEach(() => vi.unstubAllGlobals());

function harness(overrides: Partial<JourneyOptions> = {}) {
  const clock = new Clock(), controller = new AbortController();
  let serial = 0, snapshot: JourneySnapshot | undefined;
  const traces: RunTrace[] = [];
  const route = vi.fn(async (from: NavigationPoint) => ({
    coordinates: [from.coordinates, [from.coordinates[0] + .02, from.coordinates[1]] as [number, number]],
    distanceMeters: 1600, durationSeconds: 200, source: 'Test fixture', steps: [],
  }));
  const observations: { run: string; step: number; observation: DriverObservation }[] = [];
  const onScene = vi.fn(), onFrame = vi.fn();
  const options: JourneyOptions = { signal: controller.signal, route, clock, random: () => 0, uuid: () => `fixture-run-${++serial}`,
    adversaryUrl: '/api/adversary/attack', onScene, onFrame,
    onUpdate: next => { snapshot = next; }, onTrace: trace => traces.push(trace),
    driver: async (run, step, observation) => {
      observations.push({ run, step, observation });
      return { run_id: run, step, model_id: 'gemini-decision-1', request_id: `fixture-${step}`, max_steps: 60,
        decision: { action: 'accelerate', target_speed_mps: 8, confidence: 1, reason: 'Fixture driving.' } };
    }, ...overrides };
  const done = runJourney(options).then(() => null, error => error);
  return { clock, controller, options, observations, route, onScene, onFrame, traces, done, snapshot: () => snapshot!,
    stop: async () => { controller.abort(); await flush(); return done; } };
}

it('continues beyond 60 decisions in place and records separate bounded windows', async () => {
  const h = harness(); await h.clock.advance(72_000);
  expect(h.snapshot().windows).toBeGreaterThanOrEqual(2);
  expect(h.snapshot().decisions).toBeGreaterThan(60);
  expect(h.route).toHaveBeenCalledTimes(1); expect(h.onScene).toHaveBeenCalledTimes(1);
  const starts = h.observations.filter(row => row.step === 0);
  expect(starts.length).toBe(2);
  expect(starts[1].observation.remaining_m).toBeLessThan(starts[0].observation.remaining_m - 200);
  expect(starts[1].observation.speed_mps).toBeGreaterThan(0);
  expect(starts[1].run).not.toBe(starts[0].run);
  expect(h.traces[0].terminal.reason).toMatch(/time-limit|decision-limit/);
  await h.stop();
  const count = h.observations.length; await h.clock.advance(5000);
  expect(h.observations).toHaveLength(count); expect(h.clock.frames.size).toBe(0);
});

it('restarts a stalled car from a different NYC start after 25 simulated seconds', async () => {
  const h = harness({ driver: async (run, step) => ({ run_id: run, step, model_id: 'gemini-decision-1', request_id: 'fixture-stop', max_steps: 60,
    decision: { action: 'stop', target_speed_mps: 0, confidence: 1, reason: 'Stopped fixture.' } }) });
  await h.clock.advance(28_000);
  expect(h.snapshot().restarts).toBe(1); expect(h.route).toHaveBeenCalledTimes(2);
  expect(h.route.mock.calls[1][0].coordinates).not.toEqual(h.route.mock.calls[0][0].coordinates);
  expect(h.snapshot().restartsLog[0].atSeconds).toBeGreaterThanOrEqual(25);
  expect(h.snapshot().restartsLog[0].reason).toContain('Less than 2 m');
  await h.stop();
});

it('continues at the arrived route endpoint instead of teleporting on normal completion', async () => {
  const route = vi.fn(async (from: NavigationPoint) => ({ coordinates: [from.coordinates,
    [from.coordinates[0] + .0003, from.coordinates[1]] as [number, number]], distanceMeters: 25, durationSeconds: 5, source: 'Fixture', steps: [] }));
  const h = harness({ route }); await h.clock.advance(22_000);
  expect(route.mock.calls.length).toBeGreaterThan(1);
  const first = route.mock.calls[0][0].coordinates;
  expect(route.mock.calls[1][0].coordinates).toEqual([first[0] + .0003, first[1]]);
  expect(h.snapshot().restarts).toBe(0); await h.stop();
});

it('spawns a Carbon-selected event through the shared injector and retains its outcome', async () => {
  carbon(body => body.previous_attacks.length ? wait : ({ action: 'attack', event_type: 'stalled-vehicle', trigger_seconds: 3,
    parameters: { distanceAheadM: 60, approachSeconds: 2 }, durationSeconds: 4, reason: 'Block the lane fixture.' }));
  const h = harness(); await h.clock.advance(15_000);
  expect(h.onFrame.mock.calls.some(([frame]) => frame.actors.some((a: { id: string }) => a.id.startsWith('event-')))).toBe(true);
  expect(h.snapshot().attacks[0].chosenBy).toBe('carbon');
  expect(h.snapshot().attacks[0].appliedAt).toBe(3);
  expect(h.snapshot().attacks[0].reason).toBe('Block the lane fixture.');
  await h.stop(); expect(h.traces[0].adversary?.decisions.filter(row => row.decision.action === 'attack')).toHaveLength(1);
});

it('stops on Carbon or budget failure without rotating runs to retry', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ detail: { message: 'Budget exhausted.' } }), { status: 429 }));
  vi.stubGlobal('fetch', fetch);
  const h = harness(); await h.clock.advance(5000);
  expect((await h.done).message).toContain('Budget exhausted');
  expect(fetch).toHaveBeenCalledTimes(1); expect(h.route).toHaveBeenCalledTimes(1);
  expect(h.traces[0].terminal.reason).toBe('error');
});

it('ignores a Carbon response that arrives after Stop', async () => {
  let resolve!: (value: Response) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(yes => { resolve = yes; })));
  const h = harness(); await h.clock.advance(1000); await h.stop();
  const frames = h.onFrame.mock.calls.length;
  resolve(new Response(JSON.stringify({ run_id: 'fixture-run-1', attack_index: 0, model_id: 'gemini-4-carbon', request_id: 'late',
    decision: { action: 'attack', event_type: 'car-crash', trigger_seconds: 3, parameters: { distanceAheadM: 60, approachSeconds: 2 }, durationSeconds: 4, reason: 'Late fixture.' } })));
  await flush(); await h.clock.advance(5000);
  expect(h.onFrame).toHaveBeenCalledTimes(frames); expect(h.traces[0].adversary?.decisions).toHaveLength(0);
});

it('cancels a pending route without starting another driver or scene', async () => {
  let resolve!: (route: Awaited<ReturnType<JourneyOptions['route']>>) => void;
  const h = harness({ route: () => new Promise(yes => { resolve = yes; }) });
  h.controller.abort(); resolve({ coordinates: [NYC_STOPS[0].coordinates, NYC_STOPS[1].coordinates], distanceMeters: 1000, durationSeconds: 120, source: 'Fixture', steps: [] });
  await h.done; expect(h.onScene).not.toHaveBeenCalled(); expect(h.observations).toHaveLength(0);
});

it('detects a stall across window boundaries and resets the timer on meaningful progress', () => {
  const watch = new ProgressWatch({ time: 50, progress: 100 });
  expect(watch.stuck({ time: 74, progress: 101 })).toBe(false);
  expect(watch.stuck({ time: 75, progress: 101 })).toBe(true);
  expect(watch.stuck({ time: 76, progress: 103 })).toBe(false);
  expect(randomStop(NYC_STOPS[0], () => 0)).not.toBe(NYC_STOPS[0]);
});
