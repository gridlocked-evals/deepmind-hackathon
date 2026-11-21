import { parseContract } from '../contracts';
import type { ObservationBatch } from '../contracts';

export interface CaptureHistory {
  sourceId: string;
  recordingId: string;
  state: 'unverified' | 'connected' | 'stale' | 'unavailable';
  recording: boolean;
  earliestSeconds: number | null;
  latestSeconds: number | null;
  segmentBoundaries: number[];
  error: string | null;
}

export interface CaptureMedia {
  partition: 'prefix' | 'suffix';
  startSeconds: number;
  endSeconds: number;
  byteLength: number;
  sha256: string;
  frameCount: number;
  mimeType?: string;
  durationSeconds?: number;
}

export interface CaptureManifest {
  schemaVersion: '1.0';
  captureId: string;
  sourceId: string;
  modality: 'video' | 'snapshot';
  durationSeconds: number;
  allowedInterval: { startSeconds: number; endSeconds: number };
  receivedAt: string;
  sourceCapturedAt: string | null;
  sourceClock: string;
  clockUncertaintySeconds: number | null;
  originalMediaSha256: string;
  manifestSha256: string;
  byteLength: number;
  frameCount: number;
  auditedDurationSeconds?: number;
  cadence?: { repeated?: boolean };
  media: CaptureMedia[];
}

export class EvidenceError extends Error {
  constructor(message: string, readonly code: string | null = null, readonly attemptId: string | null = null) {
    super(message);
    this.name = 'EvidenceError';
  }
}

/** Fixed guidance for known server codes; provider prose never drives UI actions. */
export function observationErrorMessage(code: string | null): string | null {
  const messages: Record<string, string> = {
    model_not_configured: 'Add a Gemini API key to the backend configuration, restart the API, then retry this saved capture.',
    model_disabled: 'Model calls are disabled for this backend. Enable them before requesting observations. Your saved capture is still available.',
    model_budget_exceeded: 'The local model allowance has been reached, or this request exceeds it. Review the configured allowance, then retry this saved capture.',
    model_quota_exceeded: 'Gemini quota or rate limits blocked this call. Wait for quota to recover or check the project limits, then retry this saved capture.',
    model_timeout: 'Hydrogen did not finish before the request timed out. The attempt and source evidence were retained; check the attempt before retrying.',
    model_authentication_failed: 'The configured key cannot access Hydrogen. Check the active project key and model access, then retry this saved capture.',
    model_concurrency_limit: 'Other model calls are already running. Wait for a call to finish, then retry this saved capture.',
    busy: 'Other observation jobs are already running. Wait for a job to finish, then retry this saved capture.',
    model_incomplete_output: 'Hydrogen reached its output allowance before finishing. The incomplete result was not accepted; the attempt and source evidence are retained.',
  };
  return code ? messages[code] ?? null : null;
}

export function apiUrl(path: string): string {
  return typeof document === 'undefined' ? path : new URL(`.${path}`, document.baseURI).href;
}

const validId = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/.test(value);
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

export function parseHistory(value: unknown, sourceId: string): CaptureHistory {
  const data = value as Partial<CaptureHistory> | null;
  if (!data || data.sourceId !== sourceId || !validId(data.recordingId) || typeof data.recording !== 'boolean'
    || !['unverified', 'connected', 'stale', 'unavailable'].includes(data.state ?? '')
    || !Array.isArray(data.segmentBoundaries) || !data.segmentBoundaries.every(finite)
    || data.segmentBoundaries.some((number, index, array) => number < 0 || (index > 0 && number <= array[index - 1]))
    || !(data.earliestSeconds === null || finite(data.earliestSeconds)) || !(data.latestSeconds === null || finite(data.latestSeconds))) {
    throw new EvidenceError('The server returned invalid recording history. Refresh history to try again.');
  }
  if (data.segmentBoundaries.length && (data.earliestSeconds !== data.segmentBoundaries[0] || data.latestSeconds !== data.segmentBoundaries.at(-1))) {
    throw new EvidenceError('Recording bounds do not match the available media. Refresh history.');
  }
  if (data.segmentBoundaries.length === 1 || (!data.segmentBoundaries.length && (data.earliestSeconds !== null || data.latestSeconds !== null))) {
    throw new EvidenceError('The server returned incomplete recording bounds. Refresh history.');
  }
  return data as CaptureHistory;
}

export function parseManifest(value: unknown, sourceId: string): CaptureManifest {
  const data = value as Partial<CaptureManifest> | null;
  if (!data || data.schemaVersion !== '1.0' || data.sourceId !== sourceId || !validId(data.captureId)
    || !['video', 'snapshot'].includes(data.modality ?? '') || !finite(data.durationSeconds)
    || data.durationSeconds < 0 || data.durationSeconds > 120 || !hash(data.originalMediaSha256) || !hash(data.manifestSha256)
    || !finite(data.byteLength) || data.byteLength <= 0 || !Number.isInteger(data.frameCount) || !finite(data.frameCount) || data.frameCount <= 0
    || !['unknown', 'program_date_time', 'exposure'].includes(data.sourceClock ?? '')
    || typeof data.receivedAt !== 'string' || !Number.isFinite(Date.parse(data.receivedAt))
    || !data.allowedInterval || !finite(data.allowedInterval.startSeconds) || !finite(data.allowedInterval.endSeconds)
    || data.allowedInterval.startSeconds < 0 || data.allowedInterval.endSeconds > data.durationSeconds
    || data.allowedInterval.endSeconds < data.allowedInterval.startSeconds
    || !Array.isArray(data.media) || !data.media.length || !data.media.every(item => item && ['prefix', 'suffix'].includes(item.partition)
      && finite(item.startSeconds) && finite(item.endSeconds) && item.startSeconds >= 0 && item.endSeconds >= item.startSeconds && item.endSeconds <= data.durationSeconds!
      && finite(item.byteLength) && item.byteLength > 0 && hash(item.sha256) && Number.isInteger(item.frameCount) && item.frameCount > 0)
    || !data.media.some(item => item.partition === 'prefix')) {
    throw new EvidenceError('The capture manifest did not pass validation. No model request was made.');
  }
  if ((data.modality === 'snapshot' && data.durationSeconds !== 0)
    || (data.modality === 'video' && data.allowedInterval.endSeconds <= data.allowedInterval.startSeconds)) {
    throw new EvidenceError('The capture duration does not match its media type.');
  }
  return data as CaptureManifest;
}

async function request(path: string, options: RequestInit = {}, timeoutMs = 15000): Promise<{ value: unknown; response: Response }> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) controller.abort();
  const timer = setTimeout(abort, timeoutMs);
  try {
    const response = await fetch(apiUrl(path), { ...options, signal: controller.signal,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
    const value = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = value?.detail;
      const attempt = typeof detail?.attemptId === 'string' && validId(detail.attemptId) ? detail.attemptId : null;
      const code = typeof detail?.code === 'string' ? detail.code : null;
      const fallback = typeof detail === 'string' ? detail : typeof detail?.message === 'string' ? detail.message
        : response.status === 422 ? 'The selected input is no longer valid. Refresh history and choose another interval.'
          : `The API could not complete this request (HTTP ${response.status}).`;
      throw new EvidenceError(observationErrorMessage(code) ?? fallback, code, attempt);
    }
    return { value, response };
  } catch (error) {
    if (error instanceof EvidenceError) throw error;
    if (options.signal?.aborted) throw error;
    throw new EvidenceError(controller.signal.aborted
      ? 'The request timed out. Its outcome is unknown; check the retained attempt before sending another paid request.'
      : 'The API connection was interrupted. Check that the backend is running, then refresh.');
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', abort);
  }
}

export interface EvidenceApi {
  history(sourceId: string, signal?: AbortSignal): Promise<CaptureHistory>;
  recording(sourceId: string, action: 'start' | 'stop'): Promise<CaptureHistory>;
  seal(sourceId: string, recordingId: string, start: number, end: number): Promise<CaptureManifest>;
  snapshot(sourceId: string): Promise<CaptureManifest>;
  extract(capture: CaptureManifest): Promise<{ observation: ObservationBatch; attemptId: string | null }>;
}

export const evidenceApi: EvidenceApi = {
  async history(sourceId, signal) {
    return parseHistory((await request(`/api/captures/sources/${encodeURIComponent(sourceId)}/history`, { signal })).value, sourceId);
  },
  async recording(sourceId, action) {
    return parseHistory((await request(`/api/captures/sources/${encodeURIComponent(sourceId)}/${action}`, { method: 'POST' })).value, sourceId);
  },
  async seal(sourceId, recordingId, start, end) {
    return parseManifest((await request('/api/captures/episodes', { method: 'POST', body: JSON.stringify({
      sourceId, sceneId: sourceId, recordingId, startSeconds: start, endSeconds: end,
    }) }, 45000)).value, sourceId);
  },
  async snapshot(sourceId) {
    return parseManifest((await request('/api/captures/snapshots', { method: 'POST', body: JSON.stringify({ sourceId, sceneId: sourceId }) }, 45000)).value, sourceId);
  },
  async extract(capture) {
    const { value, response } = await request('/api/observations', { method: 'POST', body: JSON.stringify({
      captureId: capture.captureId, maxFrames: 32, includeEvents: false,
    }) }, 180000);
    const observation = parseContract('ObservationBatch', value);
    if (observation.capture.captureId !== capture.captureId || observation.capture.sourceId !== capture.sourceId
      || observation.capture.originalMediaSha256 !== capture.originalMediaSha256) throw new EvidenceError('The observation belongs to different source evidence and was rejected.');
    return { observation, attemptId: response.headers.get('X-Observation-Attempt-Id') };
  },
};

export function mediaPath(captureId: string, index: number): string {
  return `/api/captures/episodes/${encodeURIComponent(captureId)}/media/${index}`;
}
