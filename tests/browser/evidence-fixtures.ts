import { expect, type Route } from '@playwright/test';
import fixture from '../../contracts/fixtures/vertical_slice.v1.json' with { type: 'json' };
import type { Capture } from '../../src/contracts';
import type { CameraSource } from '../../src/types';
import type { SavedCapture, ExtractionReport } from '../../src/evidence/api';

export const EVIDENCE_TOKEN = 'offline-browser-operator-token-00000001';
const HASH = `sha256:${'a'.repeat(64)}`;
const PLAYLIST = '#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-ENDLIST\n';

export class EvidenceFixtures {
  private entries = new Map<string, { camera: CameraSource; saved: SavedCapture; extractionId: string; extraction?: unknown }>();
  readonly reports = new Map<string, ExtractionReport>();
  readonly reportCalls: { id: string; url: string; method: string; authorization: string | undefined }[] = [];
  readonly mediaCalls: string[] = [];
  private pending = new Map<number, Route>();
  holdReports = false;
  reportOverride: unknown = undefined;

  add(camera: CameraSource, state: 'clear' | 'unknown' | 'blocked' = 'clear', vehicles = 0, legacy = false) {
    const capture = { ...fixture.capture, id: `cap_${camera.id}`, cameraId: camera.id } as Capture;
    const saved: SavedCapture = { capture, manifest: { id: capture.id, cameraId: camera.id, sourceKind: 'hls',
      createdAt: capture.retrievalEndedAt, manifestSha256: HASH, files: { prefix: { bytes: 100, sha256: capture.mediaSha256, durationS: 30 } },
      segments: { prefix: [] }, totalDurationS: 30, labels: ['Offline browser fixture'], unknowns: [] } };
    const reference = { captureId: capture.id, sourceId: camera.id, prefixSha256: capture.mediaSha256,
      frameId: 'frame_fixture', frameIndex: 0, mediaTimeS: 0, frameSha256: HASH, roi: { x: 0, y: 0, w: 1, h: 1 } };
    const common = { confidence: .9, provenance: 'observed', disposition: 'accepted' as const, visibility: 'visible',
      time: { startS: 0, endS: 1 }, evidence: [reference] };
    const report: ExtractionReport = { schemaVersion: 'street-evidence.v1', id: `extract_${camera.id}`, captureId: capture.id,
      sourceId: camera.id, prefixSha256: capture.mediaSha256, time: { startS: 0, endS: 30 }, status: 'completed',
      facts: [{ ...common, id: 'obstruction-fixture', kind: 'obstruction', semanticClass: 'other', state, label: `Offline obstruction assessment: ${state}` },
        { ...common, id: 'count-fixture', kind: 'count', semanticClass: 'vehicle', state: 'unknown', label: 'Offline accepted vehicle count', count: { lower: vehicles, upper: vehicles } }],
      batchIds: ['obs_browser-fixture'], windows: [{ batchId: 'obs_browser-fixture', startS: 0, endS: 30, status: 'ok', unknowns: [] }],
      failures: [], metrics: { attempts: 1, acceptedFacts: 2, abstentions: 0, uncertainBoundaryEvents: 0, unprocessedWindows: 0 } };
    this.entries.set(camera.id, { camera: { ...camera, kind: 'hls', playbackUrl: `/api/browser-fixtures/cached-live/${camera.id}/index.m3u8` }, saved,
      extractionId: report.id, ...(legacy ? { extraction: { ...report, facts: [{ ...report.facts[0], label: 'UNAUTHORIZED EMBEDDED REPORT' }] } } : {}) });
    this.reports.set(report.id, report);
    return report;
  }
  camera(camera: CameraSource) { return this.entries.get(camera.id)?.camera ?? camera; }
  async handle(route: Route, local: boolean): Promise<boolean> {
    const request = route.request(), url = new URL(request.url());
    if (!local || request.method() !== 'GET') return false;
    if (url.pathname === '/api/demo-cache') {
      if (!this.entries.size) await route.fulfill({ status: 404, json: { detail: 'No offline demo cache configured' } });
      else await route.fulfill({ json: { schemaVersion: 'demo-cache.v1', createdAt: fixture.capture.retrievalEndedAt, dropped: {},
        cameras: Object.fromEntries([...this.entries].map(([id, entry]) => [id, { kind: 'hls', saved: entry.saved, extractionId: entry.extractionId,
          ...(entry.extraction ? { extraction: entry.extraction } : {}) }])) } });
      return true;
    }
    if (this.entries.size && url.pathname === '/api/edits/options') {
      await route.fulfill({ json: { presets: { protected_bike_lane: { title: 'Protected bike lane' } }, styles: { photoreal: 'supported' }, maxReferenceAttempts: 3 } }); return true;
    }
    for (const [id, entry] of this.entries) {
      if (url.pathname === `/api/captures/${id}/buffer`) {
        await route.fulfill({ json: { cameraId: id, availability: 'idle', recording: false, bufferedSeconds: 0,
          history: { segmentBoundariesS: [], maxSecondsAgo: 0, maxDurationS: 120 } } }); return true;
      }
      if (url.pathname === `/api/browser-fixtures/cached-live/${id}/index.m3u8` || url.pathname === `/api/captures/${entry.saved.capture.id}/index.m3u8`) {
        this.mediaCalls.push(url.pathname);
        await route.fulfill({ contentType: 'application/vnd.apple.mpegurl', body: PLAYLIST }); return true;
      }
      if (url.pathname === `/api/observations/extractions/${entry.extractionId}`) {
        const index = this.reportCalls.length;
        this.reportCalls.push({ id: entry.extractionId, url: request.url(), method: request.method(), authorization: request.headers().authorization });
        if (request.headers().authorization !== `Bearer ${EVIDENCE_TOKEN}`) await route.fulfill({ status: 401, json: { detail: 'Unauthorized fixture report' } });
        else if (this.holdReports) this.pending.set(index, route);
        else await route.fulfill({ json: this.reportOverride ?? this.reports.get(entry.extractionId) });
        return true;
      }
    }
    return false;
  }
  async replyReport(index: number) {
    await expect.poll(() => this.pending.has(index)).toBe(true);
    const route = this.pending.get(index)!; this.pending.delete(index);
    await route.fulfill({ json: this.reportOverride ?? this.reports.get(this.reportCalls[index].id) });
  }
  async dispose() { await Promise.allSettled([...this.pending.values()].map(route => route.abort('aborted'))); this.pending.clear(); }
}
