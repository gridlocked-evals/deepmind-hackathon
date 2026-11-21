import { describe, expect, it, vi } from 'vitest';
import type { ObservationBatch } from '../contracts';
import { EvidenceController, latestInterval, validInterval } from './controller';
import { EvidenceError, parseHistory, parseManifest, evidenceApi, observationErrorMessage } from './api';
import type { CaptureHistory, CaptureManifest, EvidenceApi } from './api';

const history = (sourceId = 'camera-one', changes: Partial<CaptureHistory> = {}): CaptureHistory => ({
  sourceId, recordingId: 'recording-one', state: 'connected', recording: true,
  earliestSeconds: 0, latestSeconds: 30, segmentBoundaries: [0, 10, 20, 30], error: null, ...changes,
});
const capture = (sourceId = 'camera-one'): CaptureManifest => ({ schemaVersion: '1.0', captureId: `capture-${sourceId}`, sourceId,
  modality: 'video', durationSeconds: 30, allowedInterval: { startSeconds: 0, endSeconds: 30 }, receivedAt: '2026-11-21T15:00:00Z',
  sourceCapturedAt: null, sourceClock: 'unknown', clockUncertaintySeconds: null, originalMediaSha256: 'a'.repeat(64), manifestSha256: 'b'.repeat(64),
  byteLength: 1000, frameCount: 50, media: [{ partition: 'prefix', startSeconds: 0, endSeconds: 30, byteLength: 1000, frameCount: 50, sha256: 'c'.repeat(64) }],
});
const observation = { id: 'observations-one', kind: 'ObservationBatch' } as ObservationBatch;
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function makeApi(): EvidenceApi {
  return {
    history: vi.fn(async sourceId => history(sourceId)),
    recording: vi.fn(async (sourceId, action) => history(sourceId, { recording: action === 'start' })),
    seal: vi.fn(async sourceId => capture(sourceId)), snapshot: vi.fn(async sourceId => ({ ...capture(sourceId), modality: 'snapshot' as const })),
    extract: vi.fn(async () => ({ observation, attemptId: 'attempt-one' })),
  };
}

describe('camera evidence workflow', () => {
  it('loads real history without invoking a paid model or fabricating history', async () => {
    const api = makeApi(), controller = new EvidenceController(api);
    controller.select({ id: 'camera-one' });
    expect(controller.getSnapshot().history).toBeNull();
    expect(controller.getSnapshot().selection).toBeNull();
    await controller.refresh();
    expect(controller.getSnapshot().selection).toEqual([0, 30]);
    expect(api.extract).not.toHaveBeenCalled();
    expect(api.seal).not.toHaveBeenCalled();
    await controller.start();
    await controller.stop();
    expect(api.recording).toHaveBeenNthCalledWith(1, 'camera-one', 'start');
    expect(api.recording).toHaveBeenNthCalledWith(2, 'camera-one', 'stop');
  });
  it('ignores a late previous-camera history response', async () => {
    const delayed = deferred<CaptureHistory>(), api = makeApi();
    api.history = vi.fn(sourceId => sourceId === 'camera-one' ? delayed.promise : Promise.resolve(history(sourceId)));
    const controller = new EvidenceController(api);
    controller.select({ id: 'camera-one' });
    const oldRequest = controller.refresh();
    controller.select({ id: 'camera-two' });
    await controller.refresh();
    delayed.resolve(history());
    await oldRequest;
    expect(controller.getSnapshot().history?.sourceId).toBe('camera-two');
    controller.select({ id: 'camera-one' });
    expect(controller.getSnapshot().history).toBeNull();
    expect(controller.getSnapshot().historyLoading).toBe(false);
  });
  it('keeps an in-flight observation with its original camera and exact capture', async () => {
    const api = makeApi(), done = deferred<{ observation: ObservationBatch; attemptId: string | null }>();
    api.extract = vi.fn(() => done.promise);
    const controller = new EvidenceController(api);
    controller.select({ id: 'camera-one' });
    await controller.refresh();
    await controller.seal();
    const source = controller.getSnapshot().capture;
    const job = controller.extract();
    controller.select({ id: 'camera-two' });
    done.resolve({ observation, attemptId: 'attempt-one' });
    await job;
    expect(controller.getSnapshot().observation).toBeNull();
    controller.select({ id: 'camera-one' });
    expect(controller.getSnapshot().observation).toBe(observation);
    expect(api.extract).toHaveBeenCalledWith(source);
    expect(controller.getSnapshot().busy).toBeNull();
  });
  it('retries only the retained capture after a model failure, with no automatic retry', async () => {
    const api = makeApi(), controller = new EvidenceController(api);
    api.extract = vi.fn().mockRejectedValueOnce(new EvidenceError('Quota reached', 'model_quota', 'attempt-failed'))
      .mockResolvedValue({ observation, attemptId: 'attempt-ok' });
    controller.select({ id: 'camera-one' });
    await controller.refresh();
    await controller.seal();
    const source = controller.getSnapshot().capture;
    await controller.extract();
    expect(controller.getSnapshot().capture).toBe(source);
    expect(controller.getSnapshot().error?.attemptId).toBe('attempt-failed');
    expect(api.extract).toHaveBeenCalledTimes(1);
    await controller.refresh();
    expect(api.extract).toHaveBeenCalledTimes(1);
    await controller.extract();
    expect(api.extract).toHaveBeenNthCalledWith(2, source);
    expect(api.seal).toHaveBeenCalledTimes(1);
    expect(controller.getSnapshot().observation).toBe(observation);
  });
  it('blocks duplicate concurrent paid clicks and out-of-range capture selection', async () => {
    const api = makeApi(), controller = new EvidenceController(api), job = deferred<{ observation: ObservationBatch; attemptId: string | null }>();
    api.extract = vi.fn(() => job.promise);
    controller.select({ id: 'camera-one' });
    await controller.refresh();
    controller.choose(0, 999);
    await controller.seal();
    expect(api.seal).not.toHaveBeenCalled();
    controller.chooseLatest();
    await controller.seal();
    const running = controller.extract();
    await controller.extract();
    expect(api.extract).toHaveBeenCalledTimes(1);
    job.resolve({ observation, attemptId: null });
    await running;
  });
  it('keeps the accepted result linked to its own attempt when a rerun fails', async () => {
    const api = makeApi(), controller = new EvidenceController(api);
    controller.select({ id: 'camera-one' });
    await controller.refresh();
    await controller.seal();
    await controller.extract();
    api.extract = vi.fn().mockRejectedValue(new EvidenceError('Quota reached', 'model_quota', 'attempt-failed'));
    await controller.extract();
    expect(controller.getSnapshot().observation).toBe(observation);
    expect(controller.getSnapshot().attemptId).toBe('attempt-one');
    expect(controller.getSnapshot().error?.attemptId).toBe('attempt-failed');
  });
  it('refreshes evicted boundaries without changing an already retained capture', async () => {
    const api = makeApi(), controller = new EvidenceController(api);
    controller.select({ id: 'camera-one' });
    await controller.refresh();
    await controller.seal();
    const source = controller.getSnapshot().capture;
    api.history = vi.fn(async () => history('camera-one', { earliestSeconds: 20, latestSeconds: 50, segmentBoundaries: [20, 30, 40, 50] }));
    await controller.refresh();
    expect(controller.getSnapshot().selection).toEqual([20, 50]);
    expect(controller.getSnapshot().capture).toBe(source);
  });
  it('does not let an older history response undo a recording stop', async () => {
    const api = makeApi(), pending = deferred<CaptureHistory>(), controller = new EvidenceController(api);
    controller.select({ id: 'camera-one' });
    await controller.refresh();
    api.history = vi.fn(() => pending.promise);
    const refresh = controller.refresh();
    await controller.stop();
    pending.resolve(history('camera-one', { recording: true }));
    await refresh;
    expect(controller.getSnapshot().history?.recording).toBe(false);
  });
  it('handles snapshot capture separately without model calls', async () => {
    const api = makeApi(), controller = new EvidenceController(api);
    controller.select({ id: 'dot-camera' });
    await controller.snapshot();
    expect(api.snapshot).toHaveBeenCalledWith('dot-camera');
    expect(api.history).not.toHaveBeenCalled();
    expect(api.extract).not.toHaveBeenCalled();
  });
});

describe('buffer and response boundaries', () => {
  it('turns known model failures into actionable guidance without hiding the retained attempt', async () => {
    const expected = {
      model_not_configured: 'Add a Gemini API key', model_disabled: 'Model calls are disabled',
      model_budget_exceeded: 'local model allowance', model_quota_exceeded: 'Gemini quota or rate limits',
      model_timeout: 'check the attempt before retrying',
    };
    for (const [code, text] of Object.entries(expected)) expect(observationErrorMessage(code)).toContain(text);
    expect(observationErrorMessage('unrecognized_error')).toBeNull();
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ detail: {
      code: 'model_not_configured', attemptId: 'attempt-retained', message: 'Generic extraction failure.',
    } }), { status: 503 }));
    vi.stubGlobal('fetch', fetcher);
    try {
      await expect(evidenceApi.extract(capture())).rejects.toMatchObject({
        message: observationErrorMessage('model_not_configured'), code: 'model_not_configured', attemptId: 'attempt-retained',
      });
      expect(fetcher).toHaveBeenCalledTimes(1);
    } finally { vi.unstubAllGlobals(); }
  });
  it('never proposes a historical interval without actual boundaries', () => {
    const empty = history('camera-one', { segmentBoundaries: [], earliestSeconds: null, latestSeconds: null });
    expect(latestInterval(empty)).toBeNull();
    expect(validInterval(empty, [0, 30])).toBe(false);
    expect(validInterval(history(), [10, 10])).toBe(false);
    expect(validInterval(history(), [0, 25])).toBe(false);
    expect(validInterval(history('camera-one', { segmentBoundaries: [0, 130], latestSeconds: 130 }), [0, 130])).toBe(false);
  });
  it('rejects source mismatches and malformed media instead of displaying success', () => {
    expect(() => parseHistory(history(), 'camera-two')).toThrow();
    expect(() => parseHistory(history('camera-one', { segmentBoundaries: [0, 10, 10] }), 'camera-one')).toThrow();
    expect(() => parseManifest(capture(), 'camera-two')).toThrow();
    expect(() => parseManifest({ ...capture(), originalMediaSha256: 'bad' }, 'camera-one')).toThrow();
    expect(() => parseManifest({ ...capture(), frameCount: undefined }, 'camera-one')).toThrow();
    expect(parseManifest(capture(), 'camera-one').captureId).toBe('capture-camera-one');
  });
  it('sends exact source identity, recording identity and selected boundaries to the server', async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _options?: RequestInit) => new Response(JSON.stringify(capture()), { status: 201 }));
    vi.stubGlobal('fetch', fetcher);
    try {
      await evidenceApi.seal('camera-one', 'recording-exact', 10, 30);
      expect(fetcher.mock.calls[0]?.[0]).toBe('/api/captures/episodes');
      const options = fetcher.mock.calls[0]?.[1] as RequestInit;
      expect(JSON.parse(String(options.body))).toEqual({ sourceId: 'camera-one', sceneId: 'camera-one', recordingId: 'recording-exact', startSeconds: 10, endSeconds: 30 });
    } finally { vi.unstubAllGlobals(); }
  });
});
