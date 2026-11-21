import { describe, expect, it } from 'vitest';
import { createRouteScenario } from '../navigation/routeScenario';
import type { ExtractionReport, EvidenceFact } from '../evidence/api';
import type { CameraSource, LngLat } from '../types';
import { evidenceConditions, withEvidenceHazard, withEvidenceTraffic } from './evidence';
import { preset } from './events';

const origin: LngLat = [-74.01, 40.72];
const camera = { id: 'cam-1', coordinates: origin } as CameraSource;
const fact = (kind: string, semanticClass: string, lower: number | null, disposition: EvidenceFact['disposition'] = 'accepted') =>
  ({ kind, semanticClass, disposition, state: kind === 'obstruction' ? 'blocked' : 'unknown', count: { lower, upper: null } }) as EvidenceFact;
const report = (facts: EvidenceFact[]) => ({ id: 'ext-1', sourceId: 'cam-1', facts }) as ExtractionReport;
const scene = (cameraId = 'cam-1', start: LngLat = origin) => createRouteScenario(
  { coordinates: [start, [start[0], start[1] + 0.003]], distanceMeters: 333, durationSeconds: 40, source: 'test', steps: [] },
  { coordinates: start, label: 'Test St' }, cameraId);

describe('camera evidence in drives', () => {
  it.each(['clear', 'unknown'])('does not turn an accepted %s obstruction assessment into a blocked hazard', state => {
    const assessment = { ...fact('obstruction', 'other', null), state };
    const conditions = evidenceConditions(report([assessment]), camera);
    expect(conditions.obstruction).toBe(false);
    const plan = preset('car-crash');
    expect(withEvidenceHazard(scene(), conditions, plan)).toBe(plan);
  });

  it('uses the largest accepted vehicle count, ignores other facts, and flags obstructions', () => {
    const conditions = evidenceConditions(report([fact('count', 'vehicle', 3), fact('count', 'vehicle', 5), fact('count', 'vehicle', 9, 'abstained'),
      fact('count', 'pedestrian', 7), fact('obstruction', 'other', null)]), camera);
    expect(conditions).toMatchObject({ vehicles: 5, obstruction: true });
    expect(evidenceConditions(report([]), camera)).toMatchObject({ vehicles: 0, obstruction: false });
    expect(() => evidenceConditions({ ...report([]), sourceId: 'other' }, camera)).toThrow();
  });

  it('adds counted cars on the ego route only for the same camera nearby', () => {
    const conditions = evidenceConditions(report([fact('count', 'vehicle', 3)]), camera);
    const result = withEvidenceTraffic(scene(), conditions);
    const cars = result.actors.filter(actor => actor.id.startsWith('evidence-car-'));
    expect(cars).toHaveLength(3);
    expect(cars.every(car => car.routeId === 'street-route')).toBe(true);
    expect(result.frames.every(frame => frame.actors.length === 4 && frame.actors.every(actor => Number.isFinite(actor.x)))).toBe(true);
    expect(withEvidenceTraffic(scene('other-cam'), conditions).actors).toHaveLength(1);
    expect(withEvidenceTraffic(scene('cam-1', [-73.99, 40.72]), conditions).actors).toHaveLength(1);
  });

  it('adds the obstruction hazard only where the evidence applies, without touching the chosen plan', () => {
    const plan = preset('car-crash');
    const blocked = evidenceConditions(report([fact('obstruction', 'other', null)]), camera);
    expect(withEvidenceHazard(scene(), blocked, plan)).toEqual(preset('stalled-vehicle'));
    expect(withEvidenceHazard(scene('cam-2'), blocked, plan)).toBe(plan);
    expect(withEvidenceHazard(scene('cam-1', [origin[0] + 0.01, origin[1]]), blocked, plan)).toBe(plan);
    expect(withEvidenceHazard(scene(), evidenceConditions(report([]), camera), plan)).toBe(plan);
  });
});
