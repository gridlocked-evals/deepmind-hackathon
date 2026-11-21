import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { JourneyOptions } from './runtime';

// Follow the real hook's callbacks across controlled reset/finalization boundaries.
// The journey runner itself is covered by runtime.test.ts with real physics.
const hooks = vi.hoisted(() => {
  const slots: any[] = [];
  let cursor = 0;
  return {
    begin() { cursor = 0; },
    clear() { slots.length = 0; cursor = 0; },
    useRef(value: unknown) { return slots[cursor++] ??= { current: value }; },
    useState(value: unknown) {
      const index = cursor++;
      slots[index] ??= { value: typeof value === 'function' ? value() : value };
      return [slots[index].value, (next: unknown) => { slots[index].value = typeof next === 'function' ? next(slots[index].value) : next; }];
    },
    useCallback(callback: unknown) { return slots[cursor++] ??= callback; },
    useEffect() {},
  };
});
vi.mock('react', () => hooks);
vi.mock('./runtime', async importOriginal => ({ ...await importOriginal<typeof import('./runtime')>(), runJourney: vi.fn() }));

import { useJourney } from './useJourney';
import { emptySnapshot, runJourney } from './runtime';
import { createDriverSimulation } from '../agent/physics';
import { demoScenario } from '../scene/DemoScenarioProvider';
import { createEvents, preset, withEventActors } from '../scenarios/events';
import { createTrace } from '../scenarios/trace';

const sessions: { options: JourneyOptions; finish: () => void }[] = [];
let exported: Blob;
function render() { hooks.begin(); return useJourney(); }
function trace(id: string) {
  const plan = preset();
  const state = { ...createDriverSimulation(withEventActors(demoScenario, plan)), events: createEvents(plan, 'baseline') };
  const result = createTrace(state, id, 'test-fixture');
  result.terminal.reason = 'interrupted';
  return result;
}
async function exportedWindows() {
  render().exportReport();
  return JSON.parse(await exported.text()).retainedWindows.map((window: { runId: string }) => window.runId);
}
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
beforeEach(() => {
  hooks.clear(); sessions.length = 0;
  vi.mocked(runJourney).mockImplementation(options => new Promise<void>(finish => { sessions.push({ options, finish }); }));
  vi.stubGlobal('document', { hidden: false, baseURI: 'http://fixture.test/', createElement: () => ({ click() {} }) });
  vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => { exported = blob as Blob; return 'blob:fixture'; });
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('retains the interrupted window after route reset without reviving its UI', async () => {
  render().start();
  const session = sessions[0];
  session.options.onUpdate({ ...emptySnapshot(), trip: 1, windows: 1, decisions: 3 });
  render().reset();
  expect(session.options.signal.aborted).toBe(true);
  // runJourney.finally executes after abort resolves the continuous driver promise.
  await Promise.resolve();
  session.options.onTrace(trace('interrupted-window'));
  session.options.onUpdate({ ...emptySnapshot(), trip: 99, windows: 99 });
  session.options.onScene(demoScenario, demoScenario.frames[0]);
  session.finish(); await flush();
  expect(await exportedWindows()).toEqual(['interrupted-window']);
  expect(render().status).toBe('idle'); expect(render().scene).toBeNull();
  expect(render().snapshot.trip).toBe(1);
});

it('does not append an abandoned journey finalizer to a newly started journey', async () => {
  render().start(); const old = sessions[0];
  render().reset(); render().start(); const fresh = sessions[1];
  old.options.onTrace(trace('old-window')); old.finish();
  fresh.options.onTrace(trace('new-window')); await flush();
  expect(await exportedWindows()).toEqual(['new-window']);
  expect(render().status).toBe('running');
});

it('keeps only four windows including the final interrupted window', async () => {
  render().start(); const session = sessions[0];
  for (let i = 1; i <= 4; i++) session.options.onTrace(trace(`window-${i}`));
  render().reset();
  session.options.onTrace(trace('window-5')); session.finish(); await flush();
  expect(await exportedWindows()).toEqual(['window-2', 'window-3', 'window-4', 'window-5']);
});
