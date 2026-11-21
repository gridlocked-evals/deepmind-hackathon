import { afterEach, describe, expect, it, vi } from 'vitest';
import { attackEvent, avoidable, nextAttackWindow, randomDecision, requestCarbon } from './adversary';
import type { AttackDecision } from './adversary';

const attack: AttackDecision = { action: 'attack', event_type: 'stalled-vehicle', trigger_seconds: 13.1,
  parameters: { distanceAheadM: 80, approachSeconds: 1 }, durationSeconds: 8, reason: 'Fixture' };

describe('shared adversary admission', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('rejects ten-second requested gaps that become nine seconds at injection barriers', () => {
    expect(() => attackEvent(attack, 1, [3.1], 6)).toThrow(/apart/);
    expect(() => attackEvent({ ...attack, trigger_seconds: 15 + 5e-10 }, 1, [6], 9)).toThrow(/apart/);
    expect(() => attackEvent({ ...attack, trigger_seconds: 15.1 }, 1, [3.1], 6)).not.toThrow();
    expect(nextAttackWindow([37.1], 39)).toBeNull(); // no second eligible barrier before the cutoff
    for (const triggerDraw of [0, .5, .999999]) {
      const draws = [.9, 0, triggerDraw, 0, 0, 0];
      let index = 0;
      const selected = randomDecision(() => draws[index++], [3.1], 6, 0);
      expect(selected.action).toBe('attack');
      expect(() => attackEvent(selected, 1, [3.1], 6)).not.toThrow();
    }
  });

  it('samples random distances from the same choice-time avoidability gate as Carbon', () => {
    const draws = [.9, 0, 0, 0, 0, 0];
    let index = 0;
    const chosen = randomDecision(() => draws[index++ % draws.length], [], 0, 11);
    expect(chosen.action).toBe('attack');
    expect(avoidable(chosen.parameters!.distanceAheadM, 11)).toBe(true);
    index = 0;
    expect(randomDecision(() => draws[index++ % draws.length], [], 0, 50).action).toBe('wait');
  });

  it('allows for acceleration before a future random attack, matching Carbon planning', () => {
    const draws = [.9, 0, .5, 0, 0, 0]; let index = 0;
    const chosen = randomDecision(() => draws[index++], [], 0, 0);
    expect(chosen.trigger_seconds).toBeGreaterThan(12);
    expect(avoidable(chosen.parameters!.distanceAheadM, 11.176)).toBe(true);
  });

  it.each([
    { model_id: 'gemini-decision-1' }, { model_id: '' }, { request_id: '' },
    { request_id: ' ' }, { max_attacks: 3 }, { max_attacks: undefined },
  ])('rejects mismatched Carbon response provenance and budget: %j', async patch => {
    const body = { run_id: 'fixture-run', attack_index: 0,
      state: { simulation_time: 0, speed_mps: 0, remaining_m: 500 }, previous_attacks: [] };
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ run_id: body.run_id, attack_index: 0,
      model_id: 'gemini-4-carbon', request_id: 'fixture-request', max_attacks: 2, decision: attack, ...patch }))));
    await expect(requestCarbon('/fixture', body, new AbortController().signal)).rejects.toThrow(/mismatched/);
  });
});
