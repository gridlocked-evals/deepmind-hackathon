import { describe, expect, it } from 'vitest';
import fixture from '../../contracts/fixtures/vertical_slice.v1.json';
import { validateVerticalSlice } from './element119';

type Json = Record<string, any>;
const mutate = (change: (d: Json) => void) => {
  const data = structuredClone(fixture) as Json;
  change(data);
  return validateVerticalSlice(data);
};
const errorsOf = (change: (d: Json) => void) => {
  const result = mutate(change);
  return result.ok ? [] : result.errors.join('\n');
};

describe('Element 119 contracts', () => {
  it('accepts the shared vertical-slice fixture', () => {
    const result = validateVerticalSlice(fixture);
    expect(result.ok ? [] : result.errors).toEqual([]);
  });

  it('rejects malformed IDs and hashes', () => {
    expect(errorsOf((d) => { d.capture.id = 'capture-1'; })).toContain('capture.id: malformed cap_ id');
    expect(errorsOf((d) => { d.capture.cameraId = '../x'; })).toContain('malformed camera id');
    expect(errorsOf((d) => { d.referenceImage.imageSha256 = 'sha256:XYZ'; })).toContain('malformed sha256');
  });

  it('rejects invalid image regions', () => {
    expect(errorsOf((d) => { d.observationBatch.observations[0].region = { kind: 'box', x: 0.9, y: 0.1, w: 0.2, h: 0.1 }; })).toContain('outside the image');
    expect(errorsOf((d) => { d.observationBatch.anchors[0].region = { kind: 'polygon', points: [[0, 0], [0.5, 0.5], [1, 1]] }; })).toContain('zero area');
  });

  it('rejects contradictory or violated permissions', () => {
    expect(errorsOf((d) => { d.streetEdit.permissions.forbidden.push('planter'); })).toContain('contradictory permissions: planter');
    expect(errorsOf((d) => { d.streetEdit.operations[0].target = 'plaza'; })).toContain('plaza, which is not allowed');
  });

  it('rejects out-of-range time references', () => {
    expect(errorsOf((d) => { d.observationBatch.observations[0].time.endS = 31; })).toContain('outside the capture');
    expect(errorsOf((d) => { d.capture.durationS = 121; })).toContain('within 0-120');
    expect(errorsOf((d) => { d.navigationEvents[1].sessionTimeS = 0.5; })).toContain('non-decreasing');
  });

  it('keeps Genie sessions distinct from deterministic simulator runs', () => {
    expect(errorsOf((d) => { d.worldSession.seed = 42; })).toContain('simulator identity fields');
    expect(errorsOf((d) => { d.worldSession.deterministicReplay = true; })).toContain('never claim deterministic replay');
  });

  it('makes gates operational and never treats unknown as accept', () => {
    expect(errorsOf((d) => { d.referenceGate.checks[0].answer = 'unknown'; })).toContain('expected unknown');
    expect(errorsOf((d) => {
      d.referenceGate.checks[0].answer = 'fail';
      Object.assign(d.referenceGate, { outcome: 'reject', effect: 'block_world_creation' });
    })).toContain('requires an accepted reference gate');
  });

  it('enforces the Hydrogen/Carbon roles and keeps unknown outcomes', () => {
    expect(errorsOf((d) => { d.streetEdit.modelCall.service = 'hydrogen'; })).toContain('expected a carbon model call');
    expect(errorsOf((d) => { d.evaluation.unknown = 0; })).toContain('unknown outcomes are never dropped');
  });
});
