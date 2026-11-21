import { describe, expect, it } from 'vitest';
import fixture from '../../fixtures/contracts/vertical-slice.json';
import invalidCases from '../../fixtures/contracts/invalid-cases.json';
import { ContractValidationError, parseContract } from './index';

function changedFixture(changes: Array<{ path: string[]; value: unknown }>): unknown {
  const value = structuredClone(fixture);
  for (const change of changes) {
    let node = value as unknown as Record<string, unknown>;
    for (const key of change.path.slice(0, -1)) node = node[key] as Record<string, unknown>;
    node[change.path.at(-1)!] = change.value;
  }
  return value;
}

describe('Element 119 wire contracts', () => {
  it('parses the same complete fixture as the Python backend', () => {
    const parsed = parseContract('VerticalSlice', fixture);
    expect(parsed.edit.trace?.modelId).toBe('gemini-4-carbon');
    expect(parseContract('ObservationBatch', parsed.observations)).toEqual(parsed.observations);
    expect(parseContract('ModelTrace', parsed.edit.trace)).toEqual(parsed.edit.trace);
    expect(parseContract('CaptureContext', parsed.observations.capture)).toEqual(parsed.observations.capture);
  });

  it.each(invalidCases)('rejects $name', (testCase) => {
    expect(() => parseContract('VerticalSlice', changedFixture(testCase.changes))).toThrow(ContractValidationError);
  });

  it('does not interpret snapshots as temporal observations', () => {
    const observations = structuredClone(fixture.observations);
    observations.capture.modality = 'snapshot';
    observations.capture.durationSeconds = 0;
    observations.capture.allowedInterval = { startSeconds: 0, endSeconds: 0 };
    observations.observations.forEach((observation) => observation.evidence.forEach((ref) => {
      ref.timeSeconds = 0;
      ref.frameIndex = 0;
    }));
    expect(parseContract('ObservationBatch', observations).capture.modality).toBe('snapshot');
    observations.observations[0].category = 'event';
    expect(() => parseContract('ObservationBatch', observations)).toThrow(/temporal events/);
  });
});
