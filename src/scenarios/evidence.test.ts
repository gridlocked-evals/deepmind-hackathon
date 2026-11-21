import { describe, expect, it } from 'vitest';
import { createRouteScenario } from '../navigation/routeScenario';
import type { ExtractionReport, EvidenceFact } from '../evidence/api';
import type { CameraSource, LngLat } from '../types';
import { sampleScenario } from '../scene/DemoScenarioProvider';
import { preset } from './events';
import { evidenceConditions, evidencePlan, withEvidenceTraffic } from './evidence';

const origin: LngLat = [-74.01, 40.72];
const camera = { id: 'cam-1', coordinates: origin } as CameraSource;
const fact = (kind: string, semanticClass: string, lower: number | null, disposition: EvidenceFact['disposition'] = 'accepted', state = 'unknown') =>
  ({ kind, semanticClass, disposition, state, count: { lower, upper: null } }) as EvidenceFact;
const report = (facts: EvidenceFact[]) => ({ id: 'ext-1', sourceId: 'cam-1', facts }) as ExtractionReport;
const scene = (cameraId = 'cam-1', start: LngLat = origin) => createRouteScenario(
  { coordinates: [start, [start[0], start[1] + 0.003]], distanceMeters: 333, durationSeconds: 40, source: 'test', steps: [] },
  { coordinates: start, label: 'Test St' }, cameraId);

describe('camera evidence in drives', () => {
  it('uses the largest accepted vehicle count, ignores other facts, and flags obstructions', () => {
    const conditions = evidenceConditions(report([fact('count', 'vehicle', 3), fact('count', 'vehicle', 5), fact('count', 'vehicle', 9, 'abstained'),
      fact('count', 'pedestrian', 7), fact('obstruction', 'other', null, 'accepted', 'blocked')]), camera);
    expect(conditions).toMatchObject({ vehicles: 5, obstruction: true });
    expect(evidenceConditions(report([]), camera)).toMatchObject({ vehicles: 0, obstruction: false });
    for (const state of ['clear', 'unknown']) expect(evidenceConditions(report([fact('obstruction', 'other', null, 'accepted', state)]), camera).obstruction).toBe(false);
    expect(() => evidenceConditions({ ...report([]), sourceId: 'other' }, camera)).toThrow();
  });

  it('adds counted cars on the ego route only for the same camera nearby', () => {
    const conditions = evidenceConditions(report([fact('count', 'vehicle', 3)]), camera);
    const result = withEvidenceTraffic(scene(), conditions);
    const cars = result.actors.filter(actor => actor.id.startsWith('evidence-car-'));
    expect(cars).toHaveLength(3);
    expect(cars.every(car => car.routeId === 'street-route')).toBe(true);
    expect(result.frames[0].actors).toHaveLength(4);
    expect(result.frames.every(frame => frame.actors.every(actor => Number.isFinite(actor.x)))).toBe(true);
    // Cars drive off the route before the preview ends, so none freeze in the held final frame.
    expect(sampleScenario(result, result.duration + 30).actors.map(actor => actor.id)).toEqual(['ego']);
    expect(withEvidenceTraffic(scene('other-cam'), conditions).actors).toHaveLength(1);
    expect(withEvidenceTraffic(scene('cam-1', [-73.99, 40.72]), conditions).actors).toHaveLength(1);
  });

  it('scopes the evidence hazard to drives the evidence applies to', () => {
    const chosen = preset('car-crash');
    const blocked = evidenceConditions(report([fact('obstruction', 'other', null, 'accepted', 'blocked')]), camera);
    expect(evidencePlan(scene(), blocked, chosen).events[0].type).toBe('stalled-vehicle');
    expect(evidencePlan(scene('other-cam'), blocked, chosen)).toBe(chosen);
    expect(evidencePlan(scene(), { ...blocked, obstruction: false }, chosen)).toBe(chosen);
    expect(evidencePlan(scene(), null, chosen)).toBe(chosen);
  });
});
