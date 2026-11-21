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

  // The same table as backend/test_adversary.py::test_requested_and_applied_attack_times_both_keep_minimum_gap,
  // so the backend's attack_barrier and the browser's barrier rounding (used by validation and the random draw) agree.
  it.each([
    [3.1, 13.1, false], [3.1, 15.0, false], [3.1, 15.1, true], [3.0, 13.0, true],
    [5.0, 15.0 + 5e-10, false], [5.0, 15.1, true], [37.1, 48.0, false], [36.0, 48.0, true],
  ])('matches the backend gap table: %d then %d accepted=%s, and random draws obey the same rule', (first, second, accepted) => {
    const check = () => attackEvent({ ...attack, trigger_seconds: second }, 1, [first], first + 1);
    if (accepted) expect(check).not.toThrow(); else expect(check).toThrow(/apart|budget/);
    if (nextAttackWindow([first], first + 1) === null) return;
    for (let i = 0; i <= 20; i++) {
      const draws = [.9, 0, i / 20 - (i === 20 ? 1e-9 : 0), 0, 0, 0];
      let index = 0;
      const chosen = randomDecision(() => draws[index++], [first], first + 1, 0);
      const barrier = (t: number) => Math.ceil((t - 1e-9) / 3) * 3;  // backend attack_barrier
      expect(chosen.trigger_seconds! - first).toBeGreaterThanOrEqual(10);
      expect(barrier(chosen.trigger_seconds!) - barrier(first)).toBeGreaterThanOrEqual(10);
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
