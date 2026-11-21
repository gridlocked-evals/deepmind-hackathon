import { describe, expect, it } from 'vitest';
import { createDemoScenario, demoScenario, DemoScenarioProvider, ROAD_MAX, ROAD_MIN, sampleScenario, STREET_BEARING } from './DemoScenarioProvider';
import type { ActorState } from '../types';

const along = (actor: ActorState) => actor.x * Math.sin(STREET_BEARING) + actor.y * Math.cos(STREET_BEARING);

describe('Vestry illustrative traffic scene', () => {
  it('reproduces a seed and keeps its known geographic identity', () => {
    expect(createDemoScenario(32998)).toEqual(demoScenario);
    expect(createDemoScenario(12).frames[100]).not.toEqual(demoScenario.frames[100]);
    expect(demoScenario.cameraId).toBe('511ny-32998');
    expect(demoScenario.origin).toEqual([-74.0114845353, 40.7228914979]);
    expect(demoScenario.provenance).toBe('demo');
    expect(demoScenario.actors).toHaveLength(25);
  });

  it('seeks without advancing or changing the source frames', () => {
    const original = JSON.stringify(demoScenario.frames[235]);
    const first = sampleScenario(demoScenario, 23.57);
    sampleScenario(demoScenario, 53);
    sampleScenario(demoScenario, 2);
    expect(sampleScenario(demoScenario, 23.57)).toEqual(first);
    expect(JSON.stringify(demoScenario.frames[235])).toBe(original);
    expect(sampleScenario(demoScenario, -5).time).toBe(0);
    expect(sampleScenario(demoScenario, 100).time).toBe(60);
    expect(sampleScenario(demoScenario, NaN).time).toBe(0);
  });

  it('keeps actors in the corridor with physical sizes and separated same-lane bodies', () => {
    for (const frame of demoScenario.frames) {
      for (const actor of frame.actors) {
        expect(along(actor)).toBeGreaterThanOrEqual(ROAD_MIN - 0.001);
        expect(along(actor)).toBeLessThanOrEqual(ROAD_MAX + 0.001);
        expect(Number.isFinite(actor.heading)).toBe(true);
        expect(actor.speed).toBeGreaterThanOrEqual(0);
        expect(actor.speed).toBeLessThan(12);
      }
      for (let i = 0; i < frame.actors.length; i++) for (let j = i + 1; j < frame.actors.length; j++) {
        const a = frame.actors[i], b = frame.actors[j];
        const da = demoScenario.actors.find(definition => definition.id === a.id)!;
        const db = demoScenario.actors.find(definition => definition.id === b.id)!;
        if (da.routeId === db.routeId) expect(Math.abs(along(a) - along(b))).toBeGreaterThan((da.length + db.length) / 2);
      }
    }
  });

  it('stops ego on red and never admits a new front bumper across a red stop line', () => {
    const stopped = sampleScenario(demoScenario, 22).actors.find(actor => actor.id === 'ego')!;
    expect(stopped.speed).toBeLessThan(0.01);
    expect(along(stopped)).toBeLessThan(-15.5);
    expect(sampleScenario(demoScenario, 38).actors.find(actor => actor.id === 'ego')!.speed).toBeGreaterThan(5);
    for (let index = 1; index < demoScenario.frames.length; index++) {
      const frame = demoScenario.frames[index];
      if (frame.signal !== 'red') continue;
      const previous = demoScenario.frames[index - 1];
      for (const actor of frame.actors) {
        const before = previous.actors.find(item => item.id === actor.id)!;
        if (Math.abs(along(actor) - along(before)) > 50) continue; // Distant corridor re-entry.
        const definition = demoScenario.actors.find(item => item.id === actor.id)!;
        const north = Number(definition.routeId.split('-')[1]) < 3;
        const direction = north ? 1 : -1, stopLine = north ? -15.5 : 17.5;
        const frontBefore = along(before) + direction * definition.length / 2;
        const frontAfter = along(actor) + direction * definition.length / 2;
        if ((stopLine - frontBefore) * direction > 0) expect((frontAfter - stopLine) * direction).toBeLessThanOrEqual(0);
      }
    }
  });

  it('does not sweep re-entering traffic through the city when interpolating', () => {
    const index = demoScenario.frames.findIndex((frame, i) => i > 0 && frame.actors.some(actor => {
      const before = demoScenario.frames[i - 1].actors.find(item => item.id === actor.id)!;
      return Math.hypot(actor.x - before.x, actor.y - before.y) > 50;
    }));
    expect(index).toBeGreaterThan(0);
    const previous = demoScenario.frames[index - 1], next = demoScenario.frames[index];
    const wrapped = next.actors.find(actor => Math.abs(along(actor) - along(previous.actors.find(item => item.id === actor.id)!)) > 50)!;
    const sampled = sampleScenario(demoScenario, (previous.time + next.time) / 2).actors.find(actor => actor.id === wrapped.id)!;
    expect(Math.abs(along(sampled))).toBeGreaterThan(180);
  });

  it('rejects unknown scenes and honours an already cancelled request', async () => {
    const provider = new DemoScenarioProvider();
    await expect(provider.loadScenario('another-camera')).rejects.toThrow('Unknown demonstration scene');
    const controller = new AbortController(); controller.abort();
    await expect(provider.loadScenario(demoScenario.id, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    await expect(provider.loadScenario(demoScenario.id)).resolves.toBe(demoScenario);
  });
});
