import { validateDecisionResponse } from '../agent/session';
import { DriverRequestError } from '../agent/trace';
import type { DriverObservation } from '../agent/types';
import { mediaUrl } from '../media/cameras';

export class JourneyRequestError extends DriverRequestError {
  constructor(message: string, readonly failure: { code: string; requestId: string | null }) { super(message); }
}

/** Preserve only bounded public failure metadata; transport/parser errors can contain raw response text. */
export async function requestJourneyDecision(run: string, step: number, observation: DriverObservation, signal: AbortSignal) {
  try {
    const response = await fetch(mediaUrl('/api/driver/decision'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
      body: JSON.stringify({ run_id: run, step, observation }) });
    const body: unknown = await response.json();
    if (!response.ok) {
      const detail = (body as { detail?: { message?: unknown; code?: unknown; request_id?: unknown } })?.detail;
      throw new JourneyRequestError(typeof detail?.message === 'string' ? detail.message.slice(0, 280) : 'The driving service could not return a decision.', {
        code: typeof detail?.code === 'string' && /^[a-z_]{1,40}$/.test(detail.code) ? detail.code : 'request_failed',
        requestId: typeof detail?.request_id === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(detail.request_id) ? detail.request_id : null,
      });
    }
    return validateDecisionResponse(body, run, step);
  } catch (error) {
    if (signal.aborted || error instanceof DriverRequestError) throw error;
    throw new DriverRequestError('The driving request failed.');
  }
}
