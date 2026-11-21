import type { ObservationBatch } from '../contracts';
import type { CameraSource } from '../types';
import { EvidenceError, evidenceApi } from './api';
import type { CaptureHistory, CaptureManifest, EvidenceApi } from './api';

export type EvidenceAction = 'starting' | 'stopping' | 'sealing' | 'snapshot' | 'extracting';
export interface EvidenceState {
  sourceId: string;
  history: CaptureHistory | null;
  historyLoading: boolean;
  selection: [number, number] | null;
  capture: CaptureManifest | null;
  observation: ObservationBatch | null;
  attemptId: string | null;
  busy: EvidenceAction | null;
  error: EvidenceError | null;
}
const initial = (sourceId: string): EvidenceState => ({ sourceId, history: null, historyLoading: false,
  selection: null, capture: null, observation: null, attemptId: null, busy: null, error: null });

export function validInterval(history: CaptureHistory | null, selection: [number, number] | null): boolean {
  return !!history && !!selection && history.segmentBoundaries.includes(selection[0]) && history.segmentBoundaries.includes(selection[1])
    && selection[1] > selection[0] && selection[1] - selection[0] <= 120;
}

export function latestInterval(history: CaptureHistory): [number, number] | null {
  const bounds = history.segmentBoundaries;
  if (bounds.length < 2) return null;
  const end = bounds[bounds.length - 1];
  const start = bounds.find(value => value >= end - 30 && value < end) ?? bounds[bounds.length - 2];
  return end - start <= 120 ? [start, end] : null;
}

/** Per-source state prevents a slow response from overwriting a newly selected camera. */
export class EvidenceController {
  private cache = new Map<string, EvidenceState>();
  private current = initial('');
  private listeners = new Set<() => void>();
  private historyRequest: { sourceId: string; controller: AbortController } | null = null;
  constructor(private api: EvidenceApi = evidenceApi) {}
  getSnapshot = () => this.current;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private update(sourceId: string, changes: Partial<EvidenceState>) {
    const next = { ...(this.cache.get(sourceId) ?? initial(sourceId)), ...changes };
    this.cache.set(sourceId, next);
    if (this.current.sourceId === sourceId) { this.current = next; this.listeners.forEach(listener => listener()); }
  }
  select(source: Pick<CameraSource, 'id'> | null) {
    const sourceId = source?.id ?? '';
    if (sourceId === this.current.sourceId) return;
    this.cancelHistory();
    this.current = this.cache.get(sourceId) ?? initial(sourceId);
    this.cache.set(sourceId, this.current);
    this.listeners.forEach(listener => listener());
  }
  cancelHistory() {
    if (this.historyRequest) {
      this.historyRequest.controller.abort();
      this.update(this.historyRequest.sourceId, { historyLoading: false });
      this.historyRequest = null;
    }
  }
  async refresh() {
    const sourceId = this.current.sourceId;
    if (!sourceId || this.historyRequest?.sourceId === sourceId || ['starting', 'stopping'].includes(this.current.busy ?? '')) return;
    this.cancelHistory();
    const request = { sourceId, controller: new AbortController() };
    this.historyRequest = request;
    this.update(sourceId, { historyLoading: true });
    try {
      const history = await this.api.history(sourceId, request.controller.signal);
      if (!request.controller.signal.aborted) this.applyHistory(sourceId, history);
    } catch (error) {
      if (!request.controller.signal.aborted) this.update(sourceId, { error: asError(error) });
    } finally {
      if (this.historyRequest === request) { this.historyRequest = null; this.update(sourceId, { historyLoading: false }); }
    }
  }
  private applyHistory(sourceId: string, history: CaptureHistory) {
    const previous = this.cache.get(sourceId)!;
    const selection = previous.history?.recordingId === history.recordingId && validInterval(history, previous.selection)
      ? previous.selection : latestInterval(history);
    this.update(sourceId, { history, selection });
  }
  choose(start: number, end: number) { this.update(this.current.sourceId, { selection: [start, end] }); }
  chooseLatest() { if (this.current.history) this.update(this.current.sourceId, { selection: latestInterval(this.current.history) }); }
  dismissError() { this.update(this.current.sourceId, { error: null }); }
  private async action(name: EvidenceAction, run: (sourceId: string, state: EvidenceState) => Promise<void>) {
    const state = this.current, sourceId = state.sourceId;
    if (!sourceId || state.busy) return;
    if (name === 'starting' || name === 'stopping') this.cancelHistory();
    this.update(sourceId, { busy: name, error: null });
    try { await run(sourceId, state); }
    catch (error) { this.update(sourceId, { error: asError(error) }); }
    finally { this.update(sourceId, { busy: null }); }
  }
  start = () => this.action('starting', async sourceId => this.applyHistory(sourceId, await this.api.recording(sourceId, 'start')));
  stop = () => this.action('stopping', async sourceId => this.applyHistory(sourceId, await this.api.recording(sourceId, 'stop')));
  seal = () => this.action('sealing', async (sourceId, state) => {
    if (!validInterval(state.history, state.selection)) throw new EvidenceError('Choose available recording boundaries covering no more than 120 seconds.');
    const capture = await this.api.seal(sourceId, state.history!.recordingId, state.selection![0], state.selection![1]);
    this.update(sourceId, { capture, observation: null, attemptId: null });
  });
  snapshot = () => this.action('snapshot', async sourceId => {
    const capture = await this.api.snapshot(sourceId);
    this.update(sourceId, { capture, observation: null, attemptId: null });
  });
  extract = () => this.action('extracting', async (sourceId, state) => {
    if (!state.capture) throw new EvidenceError('Save source evidence before requesting Hydrogen observations.');
    const result = await this.api.extract(state.capture);
    this.update(sourceId, { observation: result.observation, attemptId: result.attemptId });
  });
}
function asError(error: unknown): EvidenceError {
  return error instanceof EvidenceError ? error : new EvidenceError(error instanceof Error ? error.message : 'The request failed. Please try again.');
}
