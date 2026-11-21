import { describe, expect, it } from 'vitest';
import { demoScenario } from '../scene/DemoScenarioProvider';
import { createEvents, preset, requestEvent, withEventActors } from '../scenarios/events';
import type { ScenarioPlan } from '../scenarios/events';
import { runContinuousDriver } from './continuous';
import type { DriverClock } from './continuous';
import { createDriverSimulation } from './physics';
import type { DriverSimulation } from './physics';
import { MAX_DRIVER_STEPS } from './types';
import type { DriverObservation, DriverResponse } from './types';

async function flush() { for (let i = 0; i < 8; i++) await Promise.resolve(); }

class Clock implements DriverClock {
  constructor(private frameMilliseconds = 20) {}
  time = 0;
  id = 0;
  frames = new Map<number, (now: number) => void>();
  timers = new Map<number, { at: number; callback: () => void }>();
  now = () => this.time;
  frame = (callback: (now: number) => void) => { const id = ++this.id; this.frames.set(id, callback); return id; };
  cancelFrame = (id: number) => { this.frames.delete(id); };
  timeout = (callback: () => void, milliseconds: number) => {
    const id = ++this.id; this.timers.set(id, { at: this.time + milliseconds, callback }); return id;
  };
  clearTimeout = (id: number) => { this.timers.delete(id); };
  async advance(milliseconds: number) {
    const until = this.time + milliseconds;
    while (this.time < until) {
      this.time = Math.min(until, this.time + this.frameMilliseconds);
      for (const [id, timer] of [...this.timers]) {
        if (timer.at <= this.time && this.timers.delete(id)) timer.callback();
      }
      const frames = [...this.frames.values()]; this.frames.clear();
      for (const callback of frames) callback(this.time);
      await flush();
    }
  }
}

function initial(length = 100_000) {
  return createDriverSimulation({ ...demoScenario, id: 'continuous-route', label: 'Continuous route',
    geometryKind: 'street-route', motionSource: 'driving-route',
    geometry: { roads: [], sidewalks: [], crosswalks: [], lanes: [], route: [[0, 0], [length, 0]] },
    actors: [demoScenario.actors.find(actor => actor.role === 'ego')!],
    frames: [{ time: 0, signal: 'green', actors: [{ id: 'ego', x: 0, y: 0, heading: 0, speed: 0, braking: false }] }],
  });
}

function response(step: number): DriverResponse {
  return { run_id: 'continuous-run', step, model_id: 'gemini-decision-1', request_id: `request-${step}`, max_steps: MAX_DRIVER_STEPS,
    decision: { action: 'accelerate', target_speed_mps: 8, confidence: 0.9, reason: 'Proceed.' } };
}

function harness(state = initial(), responseDelay?: (step: number) => number, frameMilliseconds = 20) {
  const clock = new Clock(frameMilliseconds), controller = new AbortController();
  const requests: { step: number; observation: DriverObservation; signal: AbortSignal; deliver: (value: DriverResponse) => void }[] = [];
  const decisions: { observation: DriverObservation; applied: DriverSimulation }[] = [];
  let current = state;
  const result = runContinuousDriver({ initial: state, signal: controller.signal, clock,
    readState: () => current,
    requestDecision: (step, observation, signal) => new Promise(resolve => {
      requests.push({ step, observation, signal, deliver: resolve });
      if (responseDelay) clock.timeout(() => resolve(response(step)), responseDelay(step));
    }),
    onDecision: (_, observation, applied) => { decisions.push({ observation, applied }); },
    onFrame: (_, next) => { current = next; },
    onPublish: next => { current = next; },
  }).then(completion => ({ completion, error: null }), error => ({ completion: null, error }));
  return { clock, controller, requests, decisions, result, state: () => current,
    inject: (id: string) => { current = { ...current, events: requestEvent(current.events!, id, current.time) }; } };
}

function eventState(plan: ScenarioPlan) {
  return { ...createDriverSimulation(withEventActors(initial().scenario, plan)), events: createEvents(plan, 'event') };
}

describe('continuous Decisions driver', () => {
  it('keeps manual injections queued during inference and applies them before the next observation', async () => {
    const plan = preset(); plan.events[0].trigger = { kind: 'manual' };
    const run = harness(eventState(plan)); await flush();
    run.requests[0].deliver(response(0)); await flush();
    await run.clock.advance(1200);
    expect(run.requests).toHaveLength(2);
    run.inject('hazard-1');
    await run.clock.advance(400);
    expect(run.state().events!.pending['hazard-1']).toBe(1.2);
    expect(run.state().events!.applied).toHaveLength(0);
    run.requests[1].deliver(response(1)); await flush();
    await run.clock.advance(400);
    expect(run.requests).toHaveLength(3);
    expect(run.requests[1].observation.lead_gap_m).toBeNull();
    expect(run.requests[2].observation.lead_gap_m).toBeLessThan(35);
    expect(run.state().events!.applied[0].appliedAt).toBe(2);
    expect(run.state().events!.log.filter(row => row.kind === 'applied')).toHaveLength(1);
    run.controller.abort(); await run.result;
  });

  it('keeps event application time and geometry deterministic across animation cadences', async () => {
    const plan = preset(); plan.events[0].trigger = { kind: 'frame', frame: 7 };
    const fast = harness(eventState(plan), () => 0, 10), slow = harness(eventState(plan), () => 0, 50);
    await flush();
    await fast.clock.advance(9000); await slow.clock.advance(9000);
    expect(fast.state().events!.applied).toEqual(slow.state().events!.applied);
    expect(fast.state().events!.applied[0].appliedFrame).toBe(7);
    expect(fast.state().events!.applied[0].appliedAt).toBe(.7);
    expect(fast.state().events!.log).toEqual(slow.state().events!.log);
    fast.controller.abort(); slow.controller.abort(); await fast.result; await slow.result;
  });
  it('keeps moving during the next request and applies its response at the current state', async () => {
    const run = harness(); await flush();
    await run.clock.advance(400);
    expect(run.state().time).toBe(0);
    run.requests[0].deliver(response(0)); await flush();
    await run.clock.advance(700);
    expect(run.requests).toHaveLength(2);
    const waitingFrom = run.state();
    await run.clock.advance(700);
    expect(run.state().time).toBeGreaterThan(waitingFrom.time + 0.6);
    expect(run.state().progress).toBeGreaterThan(waitingFrom.progress);
    expect(run.requests).toHaveLength(2); // a single in-flight request, no overlapping polls
    run.requests[1].deliver({ ...response(1), decision: { ...response(1).decision, action: 'stop', target_speed_mps: 0 } });
    await flush();
    expect(run.decisions[1].applied.time).toBeGreaterThan(run.decisions[1].observation.simulation_time + 0.6);
    const speed = run.state().speed;
    await run.clock.advance(200);
    expect(run.state().speed).toBeLessThan(speed);
    run.controller.abort(); await run.result;
  });

  it('expires from observation capture, including latency, and aborts pending inference', async () => {
    const run = harness(); await flush();
    await run.clock.advance(800);
    run.requests[0].deliver(response(0)); await flush();
    await run.clock.advance(2000); // request 1 captured at 1000ms, delivered at 2800ms
    run.requests[1].deliver(response(1)); await flush();
    await run.clock.advance(1180);
    expect(run.requests).toHaveLength(3);
    const beforeExpiry = run.state();
    await run.clock.advance(20);
    expect((await run.result).error.message).toMatch(/command expired/);
    expect(run.requests[2].signal.aborted).toBe(true);
    expect(run.state()).toBe(beforeExpiry);
    run.requests[2].deliver(response(2)); await flush();
    await run.clock.advance(1000);
    expect(run.decisions).toHaveLength(2);
    expect(run.state()).toBe(beforeExpiry);
  });

  it('times out the initial request without moving or accepting a late result', async () => {
    const run = harness(); await flush();
    await run.clock.advance(3000);
    expect((await run.result).error.message).toMatch(/command expired/);
    expect(run.state().time).toBe(0);
    expect(run.requests[0].signal.aborted).toBe(true);
    run.requests[0].deliver(response(0)); await flush();
    expect(run.decisions).toHaveLength(0);
  });

  it('checks freshness before motion when an animation frame arrives after a long pause', async () => {
    const run = harness(); await flush();
    run.requests[0].deliver(response(0)); await flush();
    await run.clock.advance(200);
    const beforePause = run.state();
    run.clock.time = 3100; // Simulate a suspended event loop: neither timers nor frames ran.
    const frame = [...run.clock.frames.values()][0];
    frame(run.clock.time);
    expect((await run.result).error.message).toMatch(/command expired/);
    expect(run.state()).toBe(beforePause);
  });

  it('cancels immediately and makes a late response inert after Stop or route replacement', async () => {
    const run = harness(); await flush();
    run.requests[0].deliver(response(0)); await flush();
    await run.clock.advance(1200);
    const stopped = run.state();
    run.controller.abort();
    expect((await run.result).error.name).toBe('AbortError');
    expect(run.requests[1].signal.aborted).toBe(true);
    run.requests[1].deliver(response(1)); await flush();
    await run.clock.advance(1000);
    expect(run.decisions).toHaveLength(1);
    expect(run.state()).toBe(stopped);
    expect(run.clock.frames.size).toBe(0);
    expect(run.clock.timers.size).toBe(0);
  });

  it('finishes the route and cancels an in-flight next request', async () => {
    const run = harness({ ...initial(5), speed: 4 }); await flush();
    run.requests[0].deliver(response(0)); await flush();
    await run.clock.advance(3000);
    expect((await run.result).completion).toBe('route-complete');
    expect(run.requests.length).toBeGreaterThan(1);
    expect(run.clock.frames.size).toBe(0);
    expect(run.requests.every(request => request.signal.aborted || request.step === 0)).toBe(true);
  });

  it('enforces the decision budget after actually applying the final command', async () => {
    const run = harness(initial(), step => step === 0 ? 500 : 0); await flush();
    await run.clock.advance(62_000);
    expect((await run.result).completion).toBe('decision-limit');
    expect(run.requests).toHaveLength(MAX_DRIVER_STEPS);
    expect(run.decisions).toHaveLength(MAX_DRIVER_STEPS);
    expect(run.state().time).toBeGreaterThan(run.decisions.at(-1)!.applied.time + 0.9);
    expect(run.state().time).toBeLessThan(60);
  });

  it('ends at the simulation time budget while the last command remains fresh', async () => {
    const run = harness(initial(), step => step === 0 ? 0 : 700); await flush();
    await run.clock.advance(62_000);
    expect((await run.result).completion).toBe('time-limit');
    expect(run.state().time).toBeCloseTo(60, 8);
    expect(run.requests.length).toBeLessThanOrEqual(MAX_DRIVER_STEPS);
  });
});
