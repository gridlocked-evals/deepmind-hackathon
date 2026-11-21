import { advanceDriver, driverObservation, MAX_PHYSICS_STEP, prepareDriverDecision, routeComplete } from './physics';
import type { DriverSimulation } from './physics';
import { DriverRequestError } from './trace';
import { DECISION_INTERVAL_SECONDS, MAX_COMMAND_AGE_SECONDS, MAX_DRIVER_STEPS, MAX_DRIVE_SECONDS } from './types';
import type { DriverObservation, DriverResponse } from './types';

export interface DriverClock {
  now: () => number;
  frame: (callback: (now: number) => void) => number;
  cancelFrame: (id: number) => void;
  timeout: (callback: () => void, milliseconds: number) => number;
  clearTimeout: (id: number) => void;
}

export const browserClock: DriverClock = {
  now: () => performance.now(),
  frame: callback => requestAnimationFrame(callback),
  cancelFrame: id => cancelAnimationFrame(id),
  timeout: (callback, milliseconds) => window.setTimeout(callback, milliseconds),
  clearTimeout: id => window.clearTimeout(id),
};

interface ContinuousDriverOptions {
  initial: DriverSimulation;
  signal: AbortSignal;
  /** Ingest queued event changes made outside the animation loop without replacing run identity. */
  readState?: () => DriverSimulation;
  onRequest?: (step: number, observation: DriverObservation, state: DriverSimulation) => void;
  requestDecision: (step: number, observation: DriverObservation, signal: AbortSignal) => Promise<DriverResponse>;
  onDecision: (response: DriverResponse, observation: DriverObservation, state: DriverSimulation) => void;
  onFrame: (before: DriverSimulation, state: DriverSimulation) => void;
  onPublish: (state: DriverSimulation) => void;
  /** Simulation-clock barriers (e.g. the #91 adversary). A returned promise freezes physics, requests and
   * command ageing until it settles, so its latency never changes simulated timing. */
  barrier?: { everySeconds: number; until: number; run: (state: DriverSimulation) => Promise<void> | null };
  clock?: DriverClock;
}

export type DriveCompletion = 'route-complete' | 'decision-limit' | 'time-limit';

/** One animation loop keeps the last accepted command moving while the next request runs.
 * Commands expire from observation capture time, including provider latency. No request retries. */
export function runContinuousDriver(options: ContinuousDriverOptions): Promise<DriveCompletion> {
  const { initial, signal, readState, onRequest, requestDecision, onDecision, onFrame, onPublish, barrier, clock = browserClock } = options;
  return new Promise((resolve, reject) => {
    let state = initial;
    let command: DriverResponse | null = null;
    let commandCapturedAt = 0, commandAppliedAt = 0, lastRequestAt = -Infinity;
    let previousFrame: number | null = null, publishedAt = -Infinity, accumulator = 0;
    let animation = 0, expiry = 0, requestTimeout = 0, step = 0;
    let pending = false, settled = false;
    let transport: AbortController | null = null;
    // Wall time spent inside barriers is excluded from command age and frame deltas.
    let pausedMs = 0, pausedAt: number | null = null, nextBarrier = initial.time;
    // A driver response that arrives during a barrier is applied at resume, after the barrier's own state change.
    let held: (() => void) | null = null;
    const atBarrier = (current: DriverSimulation) => !!barrier && nextBarrier <= barrier.until + 1e-9 && current.time >= nextBarrier - 1e-9;
    const wallNow = () => (pausedAt ?? clock.now()) - pausedMs;
    const pause = (work: Promise<void>) => {
      pausedAt = clock.now();
      clock.clearTimeout(expiry); clock.clearTimeout(requestTimeout);
      work.then(() => {
        if (settled) return;
        pausedMs += clock.now() - pausedAt!; pausedAt = null;
        if (command) expiry = clock.timeout(stale, MAX_COMMAND_AGE_SECONDS * 1000 - (wallNow() - commandCapturedAt));
        if (pending) requestTimeout = clock.timeout(stale, MAX_COMMAND_AGE_SECONDS * 1000 - (wallNow() - lastRequestAt));
        const apply = held; held = null;
        try { apply?.(); } catch (failure) { finish(null, failure); }
      }, failure => finish(null, failure));
    };

    const finish = (completion: DriveCompletion | null, failure?: unknown) => {
      if (settled) return;
      settled = true;
      state = readState?.() ?? state;
      clock.cancelFrame(animation);
      clock.clearTimeout(expiry);
      clock.clearTimeout(requestTimeout);
      signal.removeEventListener('abort', abort);
      transport?.abort();
      onPublish(state);
      if (completion) resolve(completion); else reject(failure);
    };
    const abort = () => finish(null, new DOMException('Driving stopped', 'AbortError'));
    const stale = () => finish(null, new DriverRequestError('The next driving decision did not arrive before the current command expired. Driving stopped.'));

    const requestNext = () => {
      if (settled || pending || step >= MAX_DRIVER_STEPS) return;
      pending = true;
      const capturedAt = lastRequestAt = wallNow();
      const before = readState?.() ?? state;
      state = prepareDriverDecision(before);
      if (state !== before) onFrame(before, state);
      const observation = driverObservation(state);
      onRequest?.(step, observation, state);
      const controller = transport = new AbortController();
      requestTimeout = clock.timeout(stale, MAX_COMMAND_AGE_SECONDS * 1000);
      void Promise.resolve().then(() => {
        if (settled) return null;
        return requestDecision(step, observation, controller.signal);
      }).then(response => {
        if (settled || !response) return;
        const accept = () => {
          if (settled) return;
          const now = wallNow();
          if (now - capturedAt >= MAX_COMMAND_AGE_SECONDS * 1000
            || (command && now - commandCapturedAt >= MAX_COMMAND_AGE_SECONDS * 1000)) { stale(); return; }
          command = response;
          commandCapturedAt = capturedAt;
          commandAppliedAt = now;
          step++;
          state = readState?.() ?? state;
          onDecision(response, observation, state);
          if (previousFrame === null) previousFrame = now;
          clock.clearTimeout(expiry);
          expiry = clock.timeout(stale, MAX_COMMAND_AGE_SECONDS * 1000 - (now - capturedAt));
        };
        if (pausedAt !== null) held = accept; else accept();
      }).catch(failure => finish(null, failure)).finally(() => {
        clock.clearTimeout(requestTimeout);
        requestTimeout = 0;
        pending = false;
      });
    };

    const tick = (frameNow: number) => {
      if (settled) return;
      if (signal.aborted) { abort(); return; }
      if (pausedAt !== null) { animation = clock.frame(tick); return; }
      const now = frameNow - pausedMs;
      state = readState?.() ?? state;
      if (atBarrier(state)) {
        nextBarrier += barrier!.everySeconds;
        const work = barrier!.run(state);
        if (work) { pause(work); animation = clock.frame(tick); return; }
        // A synchronous barrier can inject an event through the shared state.
        // Continue from that update instead of overwriting it with this frame's
        // pre-barrier snapshot.
        state = readState?.() ?? state;
      }
      if (command) {
        // Check wall-clock freshness before motion, including a delayed animation frame.
        if (now - commandCapturedAt >= MAX_COMMAND_AGE_SECONDS * 1000) { stale(); return; }
        accumulator += Math.max(0, Math.min(.25, previousFrame === null ? 0 : (now - previousFrame) / 1000));
        while (accumulator >= MAX_PHYSICS_STEP - 1e-9 && state.time - initial.time < MAX_DRIVE_SECONDS - 1e-9 && !routeComplete(state) && !atBarrier(state)) {
          const before = state;
          state = advanceDriver(before, command.decision, MAX_PHYSICS_STEP);
          onFrame(before, state);
          accumulator = Math.max(0, accumulator - MAX_PHYSICS_STEP);
        }
        previousFrame = now;
        if (now - publishedAt >= 100) { onPublish(state); publishedAt = now; }
        if (routeComplete(state)) { finish('route-complete'); return; }
        if (state.time - initial.time >= MAX_DRIVE_SECONDS - 0.00001) { finish('time-limit'); return; }
        if (step >= MAX_DRIVER_STEPS && now - commandAppliedAt >= DECISION_INTERVAL_SECONDS * 1000) {
          finish('decision-limit'); return;
        }
      }
      if (!pending && now - lastRequestAt >= DECISION_INTERVAL_SECONDS * 1000) requestNext();
      animation = clock.frame(tick);
    };

    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort', abort, { once: true });
    requestNext();
    animation = clock.frame(tick);
  });
}
