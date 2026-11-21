import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { createDriverSimulation } from '../agent/physics';
import type { GeminiDriver } from '../agent/types';
import { demoScenario } from '../scene/DemoScenarioProvider';
import { ADVERSARY_PLAN } from './adversary';
import type { AdversaryDecisionRecord } from './adversary';
import { AdversaryLane } from './AdversaryLane';
import { createEvents, withEventActors } from './events';
import { createTrace } from './trace';

// An attack decided for 45 s in a run that never reached its barrier: a not_reached void.
function render(active: boolean) {
  const trace = createTrace({ ...createDriverSimulation(withEventActors(demoScenario, ADVERSARY_PLAN)), events: createEvents(ADVERSARY_PLAN, 'event') }, 'fixture-lane', 'test-fixture');
  const decision: AdversaryDecisionRecord = { attackIndex: 0, decidedAt: 0, chosenBy: 'carbon', modelId: 'gemini-4-carbon', requestId: 'r', injection: null,
    decision: { action: 'attack', event_type: 'car-crash', trigger_seconds: 45, parameters: { distanceAheadM: 60, approachSeconds: 2 }, durationSeconds: 8, reason: 'Fixture.' } };
  trace.adversary = { mode: 'carbon', seed: null, decisions: [decision], attacks: [], run: null, error: null };
  const driver = { replaying: false, trace, active } as unknown as GeminiDriver;
  return renderToStaticMarkup(<AdversaryLane settings={{ mode: 'carbon', seed: 1, guardsIgnoreHazards: true }} onChange={() => undefined} driver={driver} />);
}
const liveRegion = (html: string) => html.match(/<p class="visually-hidden" role="status" aria-live="polite">(.*?)<\/p>/)?.[1];

describe('AdversaryLane', () => {
  it('a not-reached attack is pending only while the driver is active', () => {
    expect(render(true)).toContain('pending');
    expect(render(false)).toContain('void: trigger not reached');
    expect(render(false)).not.toContain('pending');
  });
  it('shows one line per slice and announces only discrete outcome and score changes', () => {
    const html = render(false);
    expect(html).toMatch(/Unassisted: <strong>No contact 0\/0<\/strong> · cleared 0\/0 · progress 0 m · stopped-while-clear 0 s/);
    expect(html).toMatch(/Assisted: <strong>No contact 0\/0<\/strong> · cleared 0\/0/);
    const announced = liveRegion(html);
    expect(announced).toBe('Unassisted no contact 0/0, cleared 0/0. Assisted no contact 0/0, cleared 0/0. Car crash ahead: void: trigger not reached');
    expect(announced).not.toMatch(/progress|stopped/);
  });
});
