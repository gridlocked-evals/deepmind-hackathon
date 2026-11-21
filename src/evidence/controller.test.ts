import { afterEach, describe, expect, it, vi } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import fixture from '../../contracts/fixtures/vertical_slice.v1.json';
import type { Capture, GateDecision, StreetEdit, ReferenceImage } from '../contracts';
import { parseContract } from '../contracts';
import { EvidenceController, latestInterval, validInterval } from './controller';
import { canCreateWorld, evidenceApi, EvidenceError, observationErrorMessage, parseCapture, parseExtraction, parseHistory, worldApi } from './api';
import type { CaptureHistory, SavedCapture, ExtractionReport, EditBundle, ReferenceAttempt, GateRecord, EvidenceApi } from './api';
import { HumanControls } from './worldControls';
import { WorldPreview, canSendMovement } from './WorldPreview';
const token = 'operator-token-for-no-paid-unit-tests';
const hash = `sha256:${'a'.repeat(64)}`;
const cameraId = fixture.capture.cameraId;
const history = (camera = cameraId, bounds = [0, 5, 10, 15]): CaptureHistory => ({ cameraId: camera, availability: 'recording', recording: true, bufferedSeconds: bounds.at(-1)!, history: { maxSecondsAgo: bounds.at(-1)!, maxDurationS: 120, segmentBoundariesS: bounds, bufferRevision: hash } });
const saved = (camera = cameraId): SavedCapture => ({ capture: { ...fixture.capture, cameraId: camera } as Capture, manifest: { id: fixture.capture.id, cameraId: camera, sourceKind: 'hls', createdAt: fixture.capture.retrievalEndedAt, manifestSha256: hash, files: { prefix: { bytes: 100, sha256: fixture.capture.mediaSha256, durationS: 30 } }, segments: { prefix: [] }, totalDurationS: 30, labels: [], unknowns: [] } });
const report = (camera = cameraId): ExtractionReport => ({ schemaVersion: 'street-evidence.v1', id: 'extract_fixture-0001', captureId: fixture.capture.id, sourceId: camera, prefixSha256: fixture.capture.mediaSha256, time: { startS: 0, endS: 30 }, status: 'completed', facts: [], batchIds: [fixture.observationBatch.id], windows: [{ batchId: fixture.observationBatch.id, startS: 4.2, endS: 10.2, status: 'ok' }], failures: [], metrics: { attempts: 1, acceptedFacts: 1, abstentions: 0, uncertainBoundaryEvents: 0, unprocessedWindows: 0 } });
const edit: EditBundle = { streetEdit: fixture.streetEdit as StreetEdit, captureId: fixture.capture.id, captureMediaSha256: fixture.capture.mediaSha256, sourceFrame: { mediaTimeS: 4.2 }, preset: 'protected_bike_lane', imageInstructions: 'edit', worldPrompt: 'world', preservedAnchorIds: [], validationErrors: [] };
const reference: ReferenceAttempt = { id: fixture.referenceImage.id, streetEditId: fixture.streetEdit.id, captureId: fixture.capture.id, attempt: 1, style: 'photoreal', status: 'generated', referenceImage: fixture.referenceImage as ReferenceImage, modelCall: (fixture.referenceImage as ReferenceImage).modelCall, error: null };
const gate: GateRecord = { gateDecision: fixture.referenceGate as GateDecision, referenceImageId: reference.id, streetEditId: edit.streetEdit.id, state: 'accept', feedback: null };
function mockApi(): EvidenceApi {
  return { history: vi.fn(async camera => history(camera)), recording: vi.fn(async (camera, action) => ({ ...history(camera), recording: action === 'start' })),
    seal: vi.fn(async camera => saved(camera)), snapshot: vi.fn(async camera => saved(camera)), extract: vi.fn(async source => report(source.capture.cameraId)),
    options: vi.fn(async () => ({ presets: {}, styles: {}, maxReferenceAttempts: 3 })), edit: vi.fn(async () => edit), reference: vi.fn(async () => reference),
    references: vi.fn(async () => ({ attempts: [reference], gates: [gate] })), gate: vi.fn(async () => gate) };
}
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { resolve, promise }; };
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
describe('canonical capture and bounded evidence', () => {
  it('accepts empty fresh recorder [0] and actual segment boundaries', () => { expect(parseHistory(history(cameraId, [0]), cameraId).bufferedSeconds).toBe(0); expect(latestInterval(history())).toEqual([0, 15]); expect(validInterval(history(), [0, 10])).toBe(true); expect(validInterval(history(), [1, 10])).toBe(false); expect(validInterval(history(cameraId, [0, 125]), [0, 125])).toBe(false); });
  it('rejects shifted identities and corrupt media hashes', () => { expect(() => parseHistory(history(), 'another-camera')).toThrow(); expect(() => parseCapture(saved(), cameraId)).not.toThrow(); const source = saved(); source.manifest.files.prefix.sha256 = hash; expect(() => parseCapture(source, cameraId)).toThrow(); });
  it('permits an exact requested-window endpoint within prefix, excludes prefix end', () => {
    const result = report(); result.time.endS = 5;
    result.facts = [{ id: 'fact_1', kind: 'signal', semanticClass: 'signal', label: 'unknown', confidence: .5, provenance: 'observed', disposition: 'accepted', visibility: 'visible', state: 'unknown', time: { startS: 5, endS: 5 }, evidence: [{ captureId: saved().capture.id, sourceId: cameraId, prefixSha256: saved().capture.mediaSha256, frameId: 'frame_fixture-1', frameIndex: 3, mediaTimeS: 5, frameSha256: hash, roi: { x: 0, y: 0, w: 1, h: 1 } }] }];
    expect(parseExtraction(result, saved()).facts).toHaveLength(1); result.time.endS = 30; result.facts[0].evidence[0].mediaTimeS = 30; expect(() => parseExtraction(result, saved())).toThrow();
  });
  it('sends selection revision and actual offset, with no camera URL supplied by browser', async () => {
    const fetch = vi.fn<(url: string, options?: RequestInit) => Promise<Response>>(async () => new Response(JSON.stringify(saved()), { status: 200 })); vi.stubGlobal('fetch', fetch);
    await evidenceApi.seal(cameraId, history(), 5, 15);
    expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual({ cameraId, secondsAgo: 10, durationS: 10, holdoutS: 0, bufferRevision: hash });
  });
});
describe('explicit per-camera workflow', () => {
  it('retains stopped history without starting extraction', async () => { const api = mockApi(), controller = new EvidenceController(api); controller.select({ id: cameraId }); await controller.start(); await controller.stop(); expect(controller.getSnapshot().history?.recording).toBe(false); expect(validInterval(controller.getSnapshot().history, controller.getSnapshot().selection)).toBe(true); expect(api.extract).not.toHaveBeenCalled(); });
  it('ignores a stale history response after switching cameras', async () => { const api = mockApi(), pending = deferred<CaptureHistory>(); api.history = vi.fn(() => pending.promise); const controller = new EvidenceController(api); controller.select({ id: cameraId }); const running = controller.refresh(); controller.select({ id: 'other-camera' }); pending.resolve(history()); await running; expect(controller.getSnapshot().sourceId).toBe('other-camera'); expect(controller.getSnapshot().history).toBeNull(); });
  it('keeps slow paid results with their source and never retries automatically', async () => { const api = mockApi(), pending = deferred<ExtractionReport>(); api.extract = vi.fn(() => pending.promise); const controller = new EvidenceController(api); controller.select({ id: cameraId }); await controller.snapshot(); const running = controller.extract(token); await controller.extract(token); controller.select({ id: 'other-camera' }); pending.resolve(report()); await running; expect(controller.getSnapshot().extraction).toBeNull(); controller.select({ id: cameraId }); expect(controller.getSnapshot().extraction?.id).toBe(report().id); expect(api.extract).toHaveBeenCalledTimes(1); });
  it('runs the non-Genie chain using flat backend windows and original frame time', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch); const api = mockApi(), controller = new EvidenceController(api); controller.select({ id: cameraId });
    await controller.snapshot(); expect(api.extract).not.toHaveBeenCalled(); await controller.extract(token); expect(api.edit).not.toHaveBeenCalled();
    await controller.compile(fixture.observationBatch.id, 'Bike lane', 'protected_bike_lane', token);
    expect(api.edit).toHaveBeenCalledWith(saved(), fixture.observationBatch.id, 'Bike lane', 'protected_bike_lane', 4.2, token);
    expect(api.reference).not.toHaveBeenCalled(); await controller.generate('photoreal', null, token); expect(api.gate).not.toHaveBeenCalled(); await controller.gate(token);
    expect(controller.getSnapshot().gates[0].gateDecision.outcome).toBe('accept'); expect(fetch).not.toHaveBeenCalled();
  });
  it('failed rerun does not overwrite the prior extraction or replace saved media', async () => { const api = mockApi(), controller = new EvidenceController(api); controller.select({ id: cameraId }); await controller.snapshot(); await controller.extract(token); api.extract = vi.fn(async () => { throw new EvidenceError('timed out', 'timeout', 'failed-attempt'); }); await controller.extract(token); expect(controller.getSnapshot().extraction?.id).toBe(report().id); expect(controller.getSnapshot().error?.attemptId).toBe('failed-attempt'); expect(api.snapshot).toHaveBeenCalledTimes(1); });
  it('retains failed generated attempts separately from the last accepted image gate', async () => { const api = mockApi(), controller = new EvidenceController(api); controller.select({ id: cameraId }); await controller.snapshot(); await controller.extract(token); await controller.compile(fixture.observationBatch.id, 'Bike lane', 'protected_bike_lane', token); await controller.generate('photoreal', null, token); await controller.gate(token); api.reference = vi.fn(async () => ({ ...reference, id: 'ref_failed-attempt', attempt: 2, status: 'failed' as const, referenceImage: null })); await controller.generate('photoreal', null, token); expect(controller.getSnapshot().references).toHaveLength(2); expect(controller.getSnapshot().gates[0].referenceImageId).toBe(reference.id); expect(controller.getSnapshot().selectedReferenceId).toBe('ref_failed-attempt'); });
});
describe('operator auth, optional world and truthful gate', () => {
  it('uses canonical generated schema for new domain contracts', () => { expect(parseContract('StreetEdit', fixture.streetEdit).id).toBe(fixture.streetEdit.id); expect(() => parseContract('Capture', { ...fixture.capture, schemaVersion: '1.0' })).toThrow(); });
  it('keeps Bearer token in headers and blocks empty token before any paid request', async () => {
    const fetch = vi.fn<(url: string, options?: RequestInit) => Promise<Response>>(async () => new Response(JSON.stringify(report()), { status: 200 })); vi.stubGlobal('fetch', fetch);
    await expect(evidenceApi.extract(saved(), '')).rejects.toThrow('WORLD_API_TOKEN'); expect(fetch).not.toHaveBeenCalled(); await evidenceApi.extract(saved(), token);
    expect(fetch.mock.calls[0][0]).not.toContain(token); expect(fetch.mock.calls[0][1]!.headers).toMatchObject({ Authorization: `Bearer ${token}` });
  });
  it('maps disabled/budget/quota errors to actionable guidance', () => { for (const code of ['disabled', 'budget_exceeded', 'quota_exceeded', 'timeout', 'not_configured']) expect(observationErrorMessage(code)).toBeTruthy(); expect(observationErrorMessage('unknown_new_code')).toBeNull(); });
  it('unknown, rejected, mismatched and incomplete gates cannot create a world', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch); expect(canCreateWorld(gate, reference)).toBe(true);
    const unknown: GateRecord = { ...gate, gateDecision: { ...gate.gateDecision, outcome: 'unknown', effect: 'block_world_creation' } };
    expect(canCreateWorld(unknown, reference)).toBe(false); expect(canCreateWorld({ ...gate, referenceImageId: 'different' }, reference)).toBe(false);
    expect(canCreateWorld({ ...gate, gateDecision: { ...gate.gateDecision, checks: [] } }, reference)).toBe(false);
    await expect(worldApi.create(reference, unknown, token)).rejects.toThrow('accepted checks'); expect(fetch).not.toHaveBeenCalled();
  });
  it('Genie is off by default and does not claim a robotaxi or temporal judge', () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch); const html = renderToStaticMarkup(createElement(WorldPreview, { reference, gate, token, onActive: () => {} }));
    expect(html).not.toContain('checked=""'); expect(html).not.toContain('Create world · paid call'); expect(html).toContain('Evidence, edits and image review work with Genie off'); expect(fetch).not.toHaveBeenCalled();
  });
  it('approved handoff sends submission context and no secret in URL or body', async () => {
    const fetch = vi.fn<(url: string, options?: RequestInit) => Promise<Response>>(async (url: string) => new Response(JSON.stringify(url.endsWith('accepted-references') ? { acceptedReference: 'a'.repeat(64), worldPrompt: 'Bounded proposal', context: { episode_id: saved().capture.id, source_id: saved().capture.id, capture_id: saved().capture.id } } : { session_id: 'session', connect_tokens: { human: 'single-use' } }), { status: 200 })); vi.stubGlobal('fetch', fetch);
    await worldApi.create(reference, gate, token); const options = fetch.mock.calls[1][1] as RequestInit; const body = JSON.parse(options.body as string);
    expect(body.context).toEqual({ episode_id: saved().capture.id, source_id: saved().capture.id, capture_id: saved().capture.id, purpose: 'submission', prompt_id: 'interactive-world-ui-v1' }); expect(body.seconds).toBe(15); expect(body.max_usd).toBe('2.50'); expect(options.body).not.toContain(token);
  });
  it('does not start a paid world after the view closes during handoff', async () => {
    const pending = deferred<Response>(), lifetime = new AbortController();
    const fetch = vi.fn<(url: string, options?: RequestInit) => Promise<Response>>(() => pending.promise); vi.stubGlobal('fetch', fetch);
    const creating = worldApi.create(reference, gate, token, lifetime.signal);
    lifetime.abort();
    pending.resolve(new Response(JSON.stringify({ acceptedReference: 'a'.repeat(64), worldPrompt: 'Bounded proposal', context: {} }), { status: 200 }));
    await expect(creating).rejects.toThrow('cancelled before the paid session');
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toContain('/accepted-references');
  });
  it('retains a started world response for cleanup when the view closes', async () => {
    const pending = deferred<Response>(), lifetime = new AbortController();
    const fetch = vi.fn(async (url: string) => url.endsWith('accepted-references')
      ? new Response(JSON.stringify({ acceptedReference: 'a'.repeat(64), worldPrompt: 'Bounded proposal', context: {} }), { status: 200 })
      : pending.promise); vi.stubGlobal('fetch', fetch);
    const creating = worldApi.create(reference, gate, token, lifetime.signal);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    lifetime.abort();
    pending.resolve(new Response(JSON.stringify({ session_id: 'session-to-stop' }), { status: 200 }));
    await expect(creating).resolves.toMatchObject({ session_id: 'session-to-stop' });
  });
});
describe('bounded human world controls', () => {
  it('requires running state and a received source frame before movement', () => { expect(canSendMovement({ state: 'running' }, false)).toBe(false); expect(canSendMovement({ state: 'running' }, true)).toBe(true); expect(canSendMovement({ state: 'stopping' }, true)).toBe(false); });
  it('deduplicates browser repeats and holds until keyup, using 200ms expiry', () => { vi.useFakeTimers(); const send = vi.fn(), controls = new HumanControls(send); controls.keyDown('key_w'); controls.keyDown('key_w', true); vi.advanceTimersByTime(500); controls.keyDown('key_w', true); expect(send).toHaveBeenCalledTimes(1); expect(send).toHaveBeenCalledWith({ kind: 'key', owner: 'human', key: 'key_w', hold_ms: 200 }); controls.keyUp('key_w'); controls.keyUp('key_w'); expect(send).toHaveBeenCalledTimes(2); controls.keyDown('key_w'); vi.advanceTimersByTime(35); expect(send).toHaveBeenCalledTimes(3); });
  it('spaces release/takeover commands and drops pending movements on blur', () => { vi.useFakeTimers(); const send = vi.fn(), controls = new HumanControls(send); controls.keyDown('key_w'); controls.keyDown('key_a'); controls.release(); controls.takeover(); expect(send).toHaveBeenCalledTimes(1); vi.advanceTimersByTime(35); expect(send.mock.calls[1][0]).toEqual({ kind: 'release' }); vi.advanceTimersByTime(35); expect(send.mock.calls[2][0]).toEqual({ kind: 'takeover', owner: 'human' }); expect(send.mock.calls.some(([value]) => value.key === 'key_a')).toBe(false); });
  it('stops immediately and never sends queued keys after stopping', () => { vi.useFakeTimers(); const send = vi.fn(), controls = new HumanControls(send); controls.keyDown('key_w'); controls.keyDown('key_a'); controls.stop(); vi.advanceTimersByTime(500); expect(send.mock.calls.map(([value]) => value.kind)).toEqual(['key', 'stop']); });
});
