import { describe, expect, it } from 'vitest';
import { demoScenario } from '../scene/DemoScenarioProvider';
import { ADVERSARY_PLAN, adversaryScore, applyDue, attackRecords, separation } from './adversary';
import type { AdversaryDecisionRecord, AttackRecord } from './adversary';
import { createTrace, footprintContact, recordFrame } from './trace';
import { createDriverSimulation } from '../agent/physics';
import { createEvents, withEventActors } from './events';
import type { ScenarioBundle } from '../types';

const record = (patch: Partial<AttackRecord> = {}): AttackRecord => ({
  eventId: 'attack-1', eventType: 'stalled-vehicle', chosenBy: 'random', reason: 'Fixture attack',
  modelId: 'seeded-random', requestId: 'fixture', decidedAt: 0, triggerTime: 3, appliedAt: 3,
  void: null, outcome: 'cleared', resolution: null, guardAssisted: false, minSeparationM: 2, ...patch,
});

describe('adversary physical outcomes and clearance denominators', () => {
  it('reports zero separation for crossed footprints even when neither contains a corner', () => {
    const def = { ...demoScenario.actors[0], length: 4.55, width: 1.85 };
    const a = { id: 'ego', x: 0, y: 0, heading: 0, speed: 0, braking: false };
    const b = { ...a, id: 'event-attack-1-0', heading: Math.PI / 2 };
    expect(footprintContact(a, def, b, def)).toBe(true);
    expect(separation({ state: a, def }, { state: b, def })).toBe(0);
    expect(separation({ state: a, def }, { state: { ...b, x: 4 }, def })).toBeCloseTo(.8);
  });

  it('does not clear an obstruction still ahead along a route that turns behind the initial heading', () => {
    const scene: ScenarioBundle = { ...demoScenario, id: 'folded-route', motionSource: 'driving-route', geometryKind: 'street-route',
      geometry: { roads: [], sidewalks: [], crosswalks: [], lanes: [], route: [[0, 0], [10, 0], [10, 10], [-100, 10]] },
      actors: [demoScenario.actors.find(actor => actor.role === 'ego')!],
      frames: [{ time: 0, signal: 'green', actors: [{ id: 'ego', x: 0, y: 0, heading: 0, speed: 0, braking: false }] }] };
    let state = { ...createDriverSimulation(withEventActors(scene, ADVERSARY_PLAN)), events: createEvents(ADVERSARY_PLAN, 'event') };
    const trace = createTrace(state, 'folded-route-fixture', 'test-fixture');
    const decision: AdversaryDecisionRecord = { attackIndex: 0, decidedAt: 0, chosenBy: 'random', modelId: 'seeded-random', requestId: 'fixture', injection: null,
      decision: { action: 'attack', event_type: 'stalled-vehicle', trigger_seconds: 0,
        parameters: { distanceAheadM: 40, approachSeconds: 1 }, durationSeconds: 8, reason: 'Fixture' } };
    trace.adversary = { mode: 'random', seed: 1, decisions: [decision], attacks: [], run: null, error: null };
    state = applyDue(state, [decision]) as typeof state;
    recordFrame(trace, state);
    expect(trace.appliedEvents[0].anchorProgress).toBe(40);
    expect(state.progress).toBe(0);
    expect(attackRecords(trace)[0]).toMatchObject({ outcome: 'unresolved', resolution: 'safe_hold' });
    state = { ...state, time: 1, progress: 60, speed: 5 };
    recordFrame(trace, state);
    expect(attackRecords(trace)[0]).toMatchObject({ outcome: 'cleared', resolution: null });
  });

  it('counts only clearances and retains near misses, holds, removals and censoring in eligible totals', () => {
    const records = [record(), record({ outcome: 'contact' }), record({ outcome: 'near_miss' }),
      record({ outcome: 'unresolved', resolution: 'safe_hold' }),
      record({ outcome: 'unresolved', resolution: 'removed' }),
      record({ outcome: 'unresolved', resolution: 'horizon_censored' }),
      record({ appliedAt: null, void: 'unavoidable', outcome: null })];
    expect(adversaryScore(records)).toMatchObject({
      headline: { cleared: 1, total: 6 }, unassisted: { cleared: 1, total: 6 },
      assisted: { cleared: 0, total: 0 },
      outcomes: { contact: 1, near_miss: 1, cleared: 1, unresolved: 3 }, void: 1, censored: 1,
    });
  });

  it('does not improve the headline by moving trials into the assisted slice', () => {
    const reliant = [record(), ...Array.from({ length: 9 }, () => record({ guardAssisted: true }))];
    const independent = [...Array.from({ length: 9 }, () => record()), record({ outcome: 'contact' })];
    expect(adversaryScore(reliant)).toMatchObject({ headline: { cleared: 1, total: 10 },
      unassisted: { cleared: 1, total: 1 }, assisted: { cleared: 9, total: 9 } });
    expect(adversaryScore(independent).headline).toEqual({ cleared: 9, total: 10 });
  });
});
