import { parseContract } from '../contracts';
import type { Capture, GateDecision, ModelCall, ReferenceImage, StreetEdit } from '../contracts';

export interface CaptureHistory {
  cameraId: string; availability: string; recording?: boolean; bufferedSeconds: number;
  latestReceivedAt?: string; lastError?: string | null;
  history: { segmentBoundariesS?: number[]; bufferRevision?: string; maxSecondsAgo: number; maxDurationS: number };
}
export interface CaptureManifest {
  id: string; cameraId: string; sourceKind: 'snapshot' | 'hls'; createdAt: string; manifestSha256: string;
  files: { prefix: { bytes: number; sha256: string; durationS: number } };
  segments: { prefix: { durationS: number; sha256: string }[] };
  totalDurationS: number; auditedTotalDurationS?: number; labels: string[]; unknowns: { field: string; reason: string }[];
}
export interface SavedCapture { manifest: CaptureManifest; capture: Capture }
export interface Box { x: number; y: number; w: number; h: number }
export interface FrameEvidence { captureId: string; sourceId: string; prefixSha256: string; frameId: string; frameIndex: number; mediaTimeS: number; frameSha256: string; roi: Box }
export interface EvidenceFact {
  id: string; kind: string; semanticClass: string; label: string; confidence: number; provenance: string;
  disposition: 'accepted' | 'abstained' | 'boundary_uncertain' | 'duplicate'; visibility: string; state: string;
  time: { startS: number; endS: number }; evidence: FrameEvidence[];
}
export interface ModelError { code?: string; message?: string; request_id?: string | null }
export interface ExtractionReport {
  schemaVersion: 'street-evidence.v1'; id: string; captureId: string; sourceId: string; prefixSha256: string;
  time: { startS: number; endS: number }; status: string; facts: EvidenceFact[]; batchIds: string[];
  windows: { batchId?: string; startS: number; endS: number; status: string; modelCall?: ModelCall; unknowns?: { field: string; reason: string }[] }[];
  failures: { attemptId?: string; error: ModelError | string }[]; metrics: { attempts: number; acceptedFacts: number; abstentions: number; uncertainBoundaryEvents: number; unprocessedWindows: number };
}
export interface EditBundle {
  streetEdit: StreetEdit; captureId: string; captureMediaSha256: string | null; sourceFrame: { mediaTimeS?: number } | null;
  preset: string; imageInstructions: string | null; worldPrompt: string | null; preservedAnchorIds: string[]; validationErrors: string[];
}
export interface ReferenceAttempt {
  id: string; streetEditId: string; captureId: string; attempt: number; style: string; status: 'generated' | 'failed' | 'malformed';
  referenceImage: ReferenceImage | null; error: ModelError | null; modelCall: ModelCall;
}
export interface GateRecord {
  gateDecision: GateDecision; referenceImageId: string; streetEditId: string; state: 'accept' | 're_edit' | 'rejected' | 'insufficient_evidence';
  feedback: string | null; disclaimer?: string;
}
export interface EditOptions { presets: Record<string, { title: string }>; styles: Record<string, string>; maxReferenceAttempts: number }
export interface WorldStatus {
  session_id: string; state: string; frame_index: number; frames_received: number; key_owner: string;
  seconds_limit: number; stop_reason?: string | null; termination?: string | null; error?: ModelError | string | null;
}
export interface WorldCreated extends WorldStatus { websocket_path: string; connect_tokens: { human: string } }
export interface WorldResults { session: WorldStatus; frames: unknown[]; controls: unknown[]; mediaProvenance: string; temporalJudge: { status: string; uncoveredFrames: number; reason: string } }
export interface AcceptedReference { acceptedReference: string; worldPrompt: string; context: { episode_id: string; source_id: string; capture_id: string } }
export class EvidenceError extends Error {
  constructor(message: string, readonly code: string | null = null, readonly attemptId: string | null = null) { super(message); this.name = 'EvidenceError'; }
}
export function observationErrorMessage(code: string | null): string | null {
  const messages: Record<string, string> = {
    not_configured: 'Add the selected Gemini or Workshop key to the backend .env, restart the API, then retry this saved evidence.',
    disabled: 'Model calls are disabled in the backend configuration. Your saved evidence remains available.',
    budget_exceeded: 'The local model allowance has been reached, or this request exceeds it. Review the allowance before retrying the same evidence.',
    quota_exceeded: 'The provider quota or rate limit blocked this call. Check project limits or wait before retrying.',
    timeout: 'The model call timed out. Its usage and attempt are retained; review them before retrying.',
    authentication_failed: 'The configured model key cannot access this service. Check project access in the backend configuration.',
    concurrency_limit: 'Other model calls are running. Wait for a call to finish before retrying.',
    incomplete_output: 'The model reached its output allowance before finishing. The incomplete attempt was retained.',
    termination_unconfirmed: 'The provider has not confirmed the world stopped. Check its retained session before starting another.',
  };
  return code ? messages[code.replace(/^model_/, '')] ?? null : null;
}
export function apiUrl(path: string): string { return typeof document === 'undefined' ? path : new URL(`.${path}`, document.baseURI).href; }
const id = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,128}$/.test(value);
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const hash = (value: unknown): value is string => typeof value === 'string' && /^sha256:[a-f0-9]{64}$/.test(value);
export function parseHistory(value: unknown, cameraId: string): CaptureHistory {
  const data = value as CaptureHistory;
  const bounds = data?.history?.segmentBoundariesS ?? [];
  if (!data || data.cameraId !== cameraId || !finite(data.bufferedSeconds) || data.bufferedSeconds < 0 || !data.history
    || !Array.isArray(bounds) || !bounds.every(finite) || bounds.some((n, i) => n < 0 || (i > 0 && n <= bounds[i - 1]))
    || (bounds.length && (bounds[0] !== 0 || !hash(data.history.bufferRevision)))) throw new EvidenceError('Recording history did not pass validation. Refresh it before selecting media.');
  return data;
}
export function parseCapture(value: unknown, cameraId: string): SavedCapture {
  const data = value as SavedCapture;
  const capture = parseContract('Capture', data?.capture);
  if (!data.manifest || capture.cameraId !== cameraId || data.manifest.cameraId !== cameraId || data.manifest.id !== capture.id
    || data.manifest.files?.prefix?.sha256 !== capture.mediaSha256 || !hash(data.manifest.manifestSha256)
    || data.manifest.totalDurationS > 120.000001 || capture.durationS > 120.000001) throw new EvidenceError('The saved media identity or duration does not match its manifest. No model request was made.');
  return { manifest: data.manifest, capture };
}
export function parseExtraction(value: unknown, saved: SavedCapture): ExtractionReport {
  const report = value as ExtractionReport;
  if (!report || report.schemaVersion !== 'street-evidence.v1' || !id(report.id) || report.captureId !== saved.capture.id
    || report.sourceId !== saved.capture.cameraId || report.prefixSha256 !== saved.capture.mediaSha256
    || !Array.isArray(report.facts) || !Array.isArray(report.windows) || !Array.isArray(report.batchIds) || !Array.isArray(report.failures)
    || !report.time || report.time.startS < 0 || report.time.endS > saved.capture.durationS + 0.000001) throw new EvidenceError('The extraction does not match this saved source evidence.');
  for (const fact of report.facts) for (const ref of fact.evidence) {
    if (ref.captureId !== report.captureId || ref.sourceId !== report.sourceId || ref.prefixSha256 !== report.prefixSha256
      || !id(ref.frameId) || !hash(ref.frameSha256) || !finite(ref.mediaTimeS) || ref.mediaTimeS < report.time.startS
      || (saved.capture.sourceKind === 'snapshot' ? ref.mediaTimeS !== 0 : (ref.mediaTimeS > report.time.endS || ref.mediaTimeS >= saved.capture.durationS))) throw new EvidenceError('A model reference lies outside the permitted source evidence.');
  }
  return report;
}
export function errorText(error: ModelError | string | null | undefined): string {
  if (typeof error === 'string') return error;
  return observationErrorMessage(error?.code ?? null) ?? error?.message ?? 'The operation failed. The retained attempt remains available for review.';
}
export async function request(path: string, options: RequestInit = {}, token = '', timeoutMs = 20000, acceptFailure?: (value: unknown) => boolean): Promise<unknown> {
  const controller = new AbortController(); const abort = () => controller.abort();
  options.signal?.addEventListener('abort', abort, { once: true }); if (options.signal?.aborted) controller.abort();
  const timer = setTimeout(abort, timeoutMs);
  try {
    const response = await fetch(apiUrl(path), { ...options, signal: controller.signal,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers } });
    const value = await response.json().catch(() => null);
    if (!response.ok && !acceptFailure?.(value)) {
      const detail = value?.detail ?? value;
      const message = response.status === 401 ? 'Enter the operator token configured as WORLD_API_TOKEN in the backend .env.'
        : typeof detail === 'string' ? detail : errorText(detail?.code ? detail : detail?.error ?? detail);
      throw new EvidenceError(message, detail?.code ?? null, detail?.request_id ?? null);
    }
    return value;
  } catch (error) {
    if (error instanceof EvidenceError || options.signal?.aborted) throw error;
    throw new EvidenceError(controller.signal.aborted ? 'The request timed out locally. Its server outcome is unknown; refresh retained results before retrying a paid stage.' : 'The API connection was interrupted. Check that the backend is running, then refresh.');
  } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', abort); }
}
export function requireToken(token: string) { if (token.trim().length < 32) throw new EvidenceError('Enter the operator token from backend WORLD_API_TOKEN (at least 32 characters). It is separate from your Gemini keys.'); }
const post = (path: string, body: unknown, token = '', acceptFailure?: (value: unknown) => boolean) => request(path, { method: 'POST', body: JSON.stringify(body) }, token, 240000, acceptFailure);
export interface EvidenceApi {
  history(cameraId: string, signal?: AbortSignal): Promise<CaptureHistory>;
  recording(cameraId: string, action: 'start' | 'stop'): Promise<CaptureHistory>;
  seal(cameraId: string, history: CaptureHistory, start: number, end: number): Promise<SavedCapture>;
  snapshot(cameraId: string): Promise<SavedCapture>;
  extract(capture: SavedCapture, token: string): Promise<ExtractionReport>;
  options(): Promise<EditOptions>;
  edit(capture: SavedCapture, batchId: string, mayorRequest: string, preset: string, frameTimeS: number, token: string): Promise<EditBundle>;
  reference(edit: EditBundle, style: string, feedback: string | null, token: string): Promise<ReferenceAttempt>;
  references(editId: string): Promise<{ attempts: ReferenceAttempt[]; gates: GateRecord[] }>;
  gate(reference: ReferenceAttempt, token: string): Promise<GateRecord>;
}
export const evidenceApi: EvidenceApi = {
  async history(cameraId, signal) { return parseHistory(await request(`/api/captures/${encodeURIComponent(cameraId)}/buffer`, { signal }), cameraId); },
  async recording(cameraId, action) { return parseHistory(await request(`/api/captures/${encodeURIComponent(cameraId)}/${action === 'start' ? 'recording' : 'stop'}`, { method: 'POST' }), cameraId); },
  async seal(cameraId, history, start, end) {
    const latest = history.history.segmentBoundariesS?.at(-1);
    if (latest === undefined || !history.history.bufferRevision) throw new EvidenceError('Refresh recording history before sealing.');
    return parseCapture(await post('/api/captures', { cameraId, secondsAgo: latest - start, durationS: end - start, holdoutS: 0, bufferRevision: history.history.bufferRevision }), cameraId);
  },
  async snapshot(cameraId) { return parseCapture(await request(`/api/captures/snapshots/${encodeURIComponent(cameraId)}`, { method: 'POST' }, '', 45000), cameraId); },
  async extract(saved, token) { requireToken(token); return parseExtraction(await post('/api/observations/extract', { cameraId: saved.capture.cameraId, captureId: saved.capture.id }, token), saved); },
  async options() { return await request('/api/edits/options') as EditOptions; },
  async edit(saved, batchId, mayorRequest, preset, frameTimeS, token) {
    requireToken(token); const value = await post('/api/edits', { observationBatchId: batchId, mayorRequest, preset, frameTimeS }, token) as EditBundle;
    value.streetEdit = parseContract('StreetEdit', value.streetEdit);
    if (value.captureId !== saved.capture.id || value.streetEdit.observationBatchId !== batchId || (value.captureMediaSha256 && value.captureMediaSha256 !== saved.capture.mediaSha256)) throw new EvidenceError('The edit belongs to different source evidence.');
    return value;
  },
  async reference(edit, style, feedback, token) {
    requireToken(token); const result = await post('/api/references', { streetEditId: edit.streetEdit.id, style, feedback }, token,
      value => !!value && typeof value === 'object' && ['failed', 'malformed'].includes((value as ReferenceAttempt).status)) as ReferenceAttempt;
    if (result.streetEditId !== edit.streetEdit.id || result.captureId !== edit.captureId || !id(result.id)) throw new EvidenceError('The image attempt belongs to a different edit.');
    if (result.referenceImage) parseContract('ReferenceImage', result.referenceImage);
    return result;
  },
  async references(editId) { return await request(`/api/edits/${encodeURIComponent(editId)}/references`) as { attempts: ReferenceAttempt[]; gates: GateRecord[] }; },
  async gate(reference, token) {
    requireToken(token); const result = await post(`/api/references/${encodeURIComponent(reference.id)}/gate`, {}, token) as GateRecord;
    result.gateDecision = parseContract('GateDecision', result.gateDecision);
    if (result.referenceImageId !== reference.id || result.gateDecision.subject.id !== reference.id) throw new EvidenceError('The gate does not belong to the selected generated image.');
    return result;
  },
};
export function mediaPath(captureId: string): string { return `/api/captures/${encodeURIComponent(captureId)}/media`; }
export const canCreateWorld = (gate: GateRecord | null, reference: ReferenceAttempt | null): boolean => !!gate && !!reference
  && reference.status === 'generated' && gate.referenceImageId === reference.id && gate.state === 'accept'
  && gate.gateDecision.outcome === 'accept' && gate.gateDecision.effect === 'allow_world_creation'
  && gate.gateDecision.checks.length > 0 && gate.gateDecision.checks.every(check => check.answer === 'pass');
export const worldApi = {
  async create(reference: ReferenceAttempt, gate: GateRecord, token: string, signal?: AbortSignal): Promise<WorldCreated> {
    requireToken(token); if (!canCreateWorld(gate, reference)) throw new EvidenceError('World creation requires accepted checks for this exact generated image.');
    if (signal?.aborted) throw new EvidenceError('World creation was cancelled before the paid session started.');
    const handoff = await post('/api/worlds/accepted-references', { referenceImageId: reference.id, gateDecisionId: gate.gateDecision.id }, token) as AcceptedReference;
    if (signal?.aborted) throw new EvidenceError('World creation was cancelled before the paid session started.');
    // Once creation starts, keep its response so the caller can stop the exact
    // session even if the view closes while this request is in flight.
    return await post('/api/worlds', { accepted_reference: handoff.acceptedReference, context: { ...handoff.context, prompt_id: 'interactive-world-ui-v1', purpose: 'submission' }, prompt: handoff.worldPrompt, seconds: 15, max_usd: '2.50' }, token) as WorldCreated;
  },
  async stop(sessionId: string, token: string): Promise<WorldStatus> { return await post(`/api/worlds/${encodeURIComponent(sessionId)}/stop`, {}, token) as WorldStatus; },
  async results(sessionId: string, token: string): Promise<WorldResults> { return await request(`/api/worlds/${encodeURIComponent(sessionId)}/results`, {}, token) as WorldResults; },
};
