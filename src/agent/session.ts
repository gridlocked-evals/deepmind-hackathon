import { DriverRequestError } from './trace';
import { MAX_DRIVER_STEPS, SPEED_LIMIT_MPS } from './types';
import type { DriverResponse } from './types';

export interface DriverRunLease {
  controller: AbortController;
  runId: string;
}

/** Identity invalidation is separate from transport cancellation: a late server result is inert. */
export class DriverRunGate {
  private current: DriverRunLease | null = null;

  begin(runId: string): DriverRunLease {
    this.cancel();
    this.current = { controller: new AbortController(), runId };
    return this.current;
  }

  isCurrent(lease: DriverRunLease) {
    return this.current === lease && !lease.controller.signal.aborted;
  }

  cancel() {
    const previous = this.current;
    this.current = null;
    previous?.controller.abort();
  }
}

export function validateDecisionResponse(value: unknown, runId: string, step: number): DriverResponse {
  const response = value as Partial<DriverResponse> | null;
  const decision = response?.decision;
  if (!response || response.run_id !== runId || response.step !== step
    || response.max_steps !== MAX_DRIVER_STEPS
    || response.model_id !== 'gemini-decision-1'
    || typeof response.request_id !== 'string' || !response.request_id
    || !decision || !['accelerate', 'cruise', 'brake', 'stop'].includes(decision.action)
    || !Number.isFinite(decision.target_speed_mps) || decision.target_speed_mps < 0 || decision.target_speed_mps > SPEED_LIMIT_MPS
    || !Number.isFinite(decision.confidence) || decision.confidence < 0 || decision.confidence > 1
    || (decision.action === 'stop' && decision.target_speed_mps !== 0)
    || typeof decision.reason !== 'string' || !decision.reason.trim() || decision.reason.length > 240) {
    throw new DriverRequestError('Gemini returned an invalid driving decision. The simulation has stopped.');
  }
  return response as DriverResponse;
}
