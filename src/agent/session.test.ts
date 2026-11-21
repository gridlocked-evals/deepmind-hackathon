import { describe, expect, it } from 'vitest';
import { DriverRunGate, validateDecisionResponse } from './session';
import { MAX_DRIVER_STEPS } from './types';

function response() {
  return { run_id: 'test-run-123', step: 0, model_id: 'gemini-test', request_id: 'req-test', max_steps: MAX_DRIVER_STEPS,
    decision: { action: 'accelerate', target_speed_mps: 5, reason: 'Open road ahead.', confidence: 0.8 } };
}

describe('bounded driver session protocol', () => {
  it('aborts the transport and rejects late response application after Stop', async () => {
    const gate = new DriverRunGate();
    const lease = gate.begin('test-run-123');
    let deliver!: (value: unknown) => void;
    const transport = new Promise(resolve => { deliver = resolve; });
    let applied = false;
    const consumer = transport.then(value => {
      if (gate.isCurrent(lease)) { validateDecisionResponse(value, lease.runId, 0); applied = true; }
    });
    gate.cancel();
    deliver(response());
    await consumer;
    expect(lease.controller.signal.aborted).toBe(true);
    expect(applied).toBe(false);
  });

  it('invalidates the old run on restart or scenario reset, even with the same scene identity', () => {
    const gate = new DriverRunGate();
    const previous = gate.begin('test-run-123');
    const next = gate.begin('new-run-456');
    expect(gate.isCurrent(previous)).toBe(false);
    expect(previous.controller.signal.aborted).toBe(true);
    expect(gate.isCurrent(next)).toBe(true);
    gate.cancel();
    expect(gate.isCurrent(next)).toBe(false);
  });

  it('rejects mismatched identities and invalid model outputs rather than creating fallback decisions', () => {
    expect(validateDecisionResponse(response(), 'test-run-123', 0).decision.target_speed_mps).toBe(5);
    expect(() => validateDecisionResponse(response(), 'different-run', 0)).toThrow(/invalid driving decision/);
    expect(() => validateDecisionResponse(response(), 'test-run-123', 1)).toThrow(/invalid driving decision/);
    for (const decision of [
      { ...response().decision, target_speed_mps: NaN },
      { ...response().decision, target_speed_mps: 50 },
      { ...response().decision, action: 'swerve' },
      { ...response().decision, confidence: 2 },
      { ...response().decision, reason: '' },
    ]) expect(() => validateDecisionResponse({ ...response(), decision }, 'test-run-123', 0)).toThrow(/invalid driving decision/);
    expect(() => validateDecisionResponse({ ...response(), max_steps: 100 }, 'test-run-123', 0)).toThrow(/invalid driving decision/);
  });
});
