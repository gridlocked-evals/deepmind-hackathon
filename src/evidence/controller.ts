import type { CameraSource } from '../types';
import { EvidenceError, evidenceApi, parseCapture, parseExtraction } from './api';
import type { DemoCacheEntry } from './demoCache';
import type { CaptureHistory, SavedCapture, EvidenceApi, ExtractionReport, EditBundle, ReferenceAttempt, GateRecord } from './api';
export type EvidenceAction = 'starting' | 'stopping' | 'sealing' | 'snapshot' | 'extracting' | 'compiling' | 'generating' | 'gating' | 'refreshing';
export interface EvidenceState {
  sourceId: string; history: CaptureHistory | null; historyLoading: boolean; selection: [number, number] | null;
  capture: SavedCapture | null; extraction: ExtractionReport | null; extractions: ExtractionReport[];
  edit: EditBundle | null; references: ReferenceAttempt[]; gates: GateRecord[]; selectedReferenceId: string | null;
  busy: EvidenceAction | null; error: EvidenceError | null;
}
const downstream = { extraction: null, extractions: [], edit: null, references: [], gates: [], selectedReferenceId: null };
const initial = (sourceId: string): EvidenceState => ({ sourceId, history: null, historyLoading: false,
  selection: null, capture: null, ...downstream, busy: null, error: null });
export function validInterval(history: CaptureHistory | null, selection: [number, number] | null): boolean {
  const bounds = history?.history.segmentBoundariesS ?? [];
  return !!history?.history.bufferRevision && !!selection && bounds.includes(selection[0]) && bounds.includes(selection[1])
    && selection[1] > selection[0] && selection[1] - selection[0] <= 120;
}
export function latestInterval(history: CaptureHistory): [number, number] | null {
  const bounds = history.history.segmentBoundariesS ?? []; if (bounds.length < 2) return null;
  const end = bounds[bounds.length - 1]; const start = bounds.find(value => value >= end - 30 && value < end) ?? bounds[bounds.length - 2];
  return end - start <= 120 ? [start, end] : null;
}
export function selectedReference(state: EvidenceState): ReferenceAttempt | null { return state.references.find(ref => ref.id === state.selectedReferenceId) ?? null; }
export function selectedGate(state: EvidenceState): GateRecord | null { return state.gates.find(gate => gate.referenceImageId === state.selectedReferenceId) ?? null; }
/** Mutating results stay with their camera; switching views never launches or retries a model call. */
export class EvidenceController {
  private cache = new Map<string, EvidenceState>(); private current = initial(''); private listeners = new Set<() => void>();
  private historyRequest: { sourceId: string; controller: AbortController } | null = null;
  constructor(private api: EvidenceApi = evidenceApi) {}
  getSnapshot = () => this.current;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private update(sourceId: string, changes: Partial<EvidenceState>) {
    const next = { ...(this.cache.get(sourceId) ?? initial(sourceId)), ...changes }; this.cache.set(sourceId, next);
    if (this.current.sourceId === sourceId) { this.current = next; this.listeners.forEach(listener => listener()); }
  }
  select(source: Pick<CameraSource, 'id'> | null) {
    const sourceId = source?.id ?? ''; if (sourceId === this.current.sourceId) return;
    this.cancelHistory(); this.current = this.cache.get(sourceId) ?? initial(sourceId); this.cache.set(sourceId, this.current); this.listeners.forEach(listener => listener());
  }
  cancelHistory() { if (this.historyRequest) { this.historyRequest.controller.abort(); this.update(this.historyRequest.sourceId, { historyLoading: false }); this.historyRequest = null; } }
  async refresh() {
    const sourceId = this.current.sourceId; if (!sourceId || this.historyRequest?.sourceId === sourceId || this.current.busy) return;
    this.cancelHistory(); const request = { sourceId, controller: new AbortController() }; this.historyRequest = request; this.update(sourceId, { historyLoading: true });
    try { const history = await this.api.history(sourceId, request.controller.signal); if (!request.controller.signal.aborted) this.applyHistory(sourceId, history); }
    catch (error) { if (!request.controller.signal.aborted) this.update(sourceId, { error: asError(error) }); }
    finally { if (this.historyRequest === request) { this.historyRequest = null; this.update(sourceId, { historyLoading: false }); } }
  }
  private applyHistory(sourceId: string, history: CaptureHistory) {
    const previous = this.cache.get(sourceId)!;
    // Cumulative bounds shift when the ring evicts; an exact revision is required to retain a choice.
    const selection = previous.history?.history.bufferRevision === history.history.bufferRevision && validInterval(history, previous.selection) ? previous.selection : latestInterval(history);
    this.update(sourceId, { history, selection });
  }
  choose(start: number, end: number) { this.update(this.current.sourceId, { selection: [start, end] }); }
  chooseLatest() { if (this.current.history) this.update(this.current.sourceId, { selection: latestInterval(this.current.history) }); }
  chooseReference(id: string) { if (this.current.references.some(ref => ref.id === id)) this.update(this.current.sourceId, { selectedReferenceId: id }); }
  dismissError() { this.update(this.current.sourceId, { error: null }); }
  private async action(name: EvidenceAction, run: (sourceId: string, state: EvidenceState) => Promise<void>) {
    const state = this.current, sourceId = state.sourceId; if (!sourceId || state.busy) return;
    this.cancelHistory(); this.update(sourceId, { busy: name, error: null });
    try { await run(sourceId, state); } catch (error) { this.update(sourceId, { error: asError(error) }); } finally { this.update(sourceId, { busy: null }); }
  }
  start = () => this.action('starting', async sourceId => this.applyHistory(sourceId, await this.api.recording(sourceId, 'start')));
  stop = () => this.action('stopping', async sourceId => this.applyHistory(sourceId, await this.api.recording(sourceId, 'stop')));
  seal = () => this.action('sealing', async (sourceId, state) => {
    if (!validInterval(state.history, state.selection)) throw new EvidenceError('Choose whole recorded segments covering no more than 120 seconds.');
    const capture = await this.api.seal(sourceId, state.history!, state.selection![0], state.selection![1]); this.update(sourceId, { capture, ...downstream });
  });
  loadCached = (entry: DemoCacheEntry) => this.action('refreshing', async sourceId => {
    const capture = parseCapture(entry.saved, sourceId), extraction = entry.extraction ? parseExtraction(entry.extraction, capture) : null;
    this.update(sourceId, { capture, ...downstream, ...(extraction && { extraction, extractions: [extraction] }) });
  });
  snapshot = () => this.action('snapshot', async sourceId => { const capture = await this.api.snapshot(sourceId); this.update(sourceId, { capture, ...downstream }); });
  extract = (token: string) => this.action('extracting', async (sourceId, state) => {
    if (!state.capture) throw new EvidenceError('Save source evidence before requesting observations.');
    const extraction = await this.api.extract(state.capture, token); this.update(sourceId, { extraction, extractions: [...state.extractions, extraction] });
  });
  compile = (batchId: string, mayorRequest: string, preset: string, token: string) => this.action('compiling', async (sourceId, state) => {
    const report = state.extractions.find(report => report.batchIds.includes(batchId));
    if (!state.capture || !report) throw new EvidenceError('Choose an observation batch from this saved capture.');
    const frameTimeS = report.windows.find(window => window.batchId === batchId)?.startS ?? 0;
    const edit = await this.api.edit(state.capture, batchId, mayorRequest, preset, frameTimeS, token);
    this.update(sourceId, { edit, references: [], gates: [], selectedReferenceId: null });
  });
  generate = (style: string, feedback: string | null, token: string) => this.action('generating', async (sourceId, state) => {
    if (!state.edit || state.edit.streetEdit.status !== 'compiled') throw new EvidenceError('Compile a valid edit before generating a reference.');
    const result = await this.api.reference(state.edit, style, feedback, token);
    this.update(sourceId, { references: [...state.references, result], selectedReferenceId: result.id });
  });
  refreshReferences = () => this.action('refreshing', async (sourceId, state) => {
    if (!state.edit) return; const results = await this.api.references(state.edit.streetEdit.id);
    if (results.attempts.some(ref => ref.streetEditId !== state.edit!.streetEdit.id) || results.gates.some(gate => gate.streetEditId !== state.edit!.streetEdit.id)) throw new EvidenceError('The retained attempts belong to another edit.');
    this.update(sourceId, { references: results.attempts, gates: results.gates,
      selectedReferenceId: results.attempts.some(ref => ref.id === state.selectedReferenceId) ? state.selectedReferenceId : results.attempts.at(-1)?.id ?? null });
  });
  gate = (token: string) => this.action('gating', async (sourceId, state) => {
    const reference = selectedReference(state); if (!reference || reference.status !== 'generated') throw new EvidenceError('Select a generated image before requesting a gate.');
    const gate = await this.api.gate(reference, token);
    this.update(sourceId, { gates: [...state.gates.filter(item => item.referenceImageId !== reference.id), gate] });
  });
}
function asError(error: unknown): EvidenceError { return error instanceof EvidenceError ? error : new EvidenceError(error instanceof Error ? error.message : 'The request failed. Please try again.'); }
