import { advanceDriver, driverObservation, MAX_PHYSICS_STEP, routeComplete } from './physics';
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

const browserClock: DriverClock = {
  now: () => performance.now(),
  frame: callback => requestAnimationFrame(callback),
  cancelFrame: id => cancelAnimationFrame(id),
  timeout: (callback, milliseconds) => window.setTimeout(callback, milliseconds),
  clearTimeout: id => window.clearTimeout(id),
};

interface ContinuousDriverOptions {
  initial: DriverSimulation;
  signal: AbortSignal;
  requestDecision: (step: number, observation: DriverObservation, signal: AbortSignal) => Promise<DriverResponse>;
  onDecision: (response: DriverResponse, observation: DriverObservation, state: DriverSimulation) => void;
  onFrame: (before: DriverSimulation, state: DriverSimulation) => void;
  onPublish: (state: DriverSimulation) => void;
  clock?: DriverClock;
}

export type DriveCompletion = 'route-complete' | 'decision-limit' | 'time-limit';

/** One animation loop keeps the last accepted command moving while the next request runs.
 * Commands expire from observation capture time, including provider latency. No request retries. */
export function runContinuousDriver(options: ContinuousDriverOptions): Promise<DriveCompletion> {
  const { initial, signal, requestDecision, onDecision, onFrame, onPublish, clock = browserClock } = options;
  return new Promise((resolve, reject) => {
    let state = initial;
    let command: DriverResponse | null = null;
    let commandCapturedAt = 0, commandAppliedAt = 0, lastRequestAt = -Infinity;
    let previousFrame: number | null = null, publishedAt = -Infinity;
    let animation = 0, expiry = 0, requestTimeout = 0, step = 0;
    let pending = false, settled = false;
    let transport: AbortController | null = null;

    const finish = (completion: DriveCompletion | null, failure?: unknown) => {
      if (settled) return;
      settled = true;
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
      const capturedAt = lastRequestAt = clock.now();
      const observation = driverObservation(state);
      const controller = transport = new AbortController();
      requestTimeout = clock.timeout(stale, MAX_COMMAND_AGE_SECONDS * 1000);
      void Promise.resolve().then(() => {
        if (settled) return null;
        return requestDecision(step, observation, controller.signal);
      }).then(response => {
        if (settled || !response) return;
        const now = clock.now();
        if (now - capturedAt >= MAX_COMMAND_AGE_SECONDS * 1000
          || (command && now - commandCapturedAt >= MAX_COMMAND_AGE_SECONDS * 1000)) { stale(); return; }
        command = response;
        commandCapturedAt = capturedAt;
        commandAppliedAt = now;
        step++;
        onDecision(response, observation, state);
        if (previousFrame === null) previousFrame = now;
        clock.clearTimeout(expiry);
        expiry = clock.timeout(stale, MAX_COMMAND_AGE_SECONDS * 1000 - (now - capturedAt));
      }).catch(failure => finish(null, failure)).finally(() => {
        clock.clearTimeout(requestTimeout);
        pending = false;
      });
    };

    const tick = (now: number) => {
      if (settled) return;
      if (signal.aborted) { abort(); return; }
      if (command) {
        // Check wall-clock freshness before motion, including a delayed animation frame.
        if (now - commandCapturedAt >= MAX_COMMAND_AGE_SECONDS * 1000) { stale(); return; }
        const delta = Math.max(0, Math.min(MAX_PHYSICS_STEP, previousFrame === null ? 0 : (now - previousFrame) / 1000,
          MAX_DRIVE_SECONDS - (state.time - initial.time)));
        const before = state;
        state = advanceDriver(before, command.decision, delta);
        onFrame(before, state);
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
