import type { ComponentProps } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

// Execute the component's actual input handlers with controlled commit/unmount
// boundaries and delayed File.text(); no browser files or model calls are used.
const hooks = vi.hoisted(() => {
  const slots: any[] = [];
  const effects: (() => void)[] = [];
  let cursor = 0;
  return {
    begin() { cursor = 0; },
    clear() { slots.length = 0; effects.length = 0; cursor = 0; },
    commit() { effects.splice(0).forEach(run => run()); },
    unmount() { slots.forEach(slot => slot.cleanup?.()); },
    useRef(value: unknown) { return slots[cursor++] ??= { current: value }; },
    useState(value: unknown) {
      const index = cursor++;
      slots[index] ??= { value };
      return [slots[index].value, (next: unknown) => { slots[index].value = next; }];
    },
    useLayoutEffect(effect: () => (() => void) | void, deps: unknown[]) {
      const index = cursor++, previous = slots[index];
      if (!previous || deps.some((value, i) => value !== previous.deps[i])) {
        effects.push(() => { previous?.cleanup?.(); slots[index] = { deps, cleanup: effect() }; });
      }
    },
  };
});
vi.mock('react', () => hooks);

import { ScenarioPanel } from './ScenarioPanel';
import { createDriverSimulation } from '../agent/physics';
import type { GeminiDriver } from '../agent/types';
import { demoScenario } from '../scene/DemoScenarioProvider';
import { createEvents, preset, withEventActors } from './events';
import { createTrace } from './trace';

type Element = { type: unknown; props: Record<string, any> };
let props: ComponentProps<typeof ScenarioPanel>;
function render() { hooks.begin(); const tree = ScenarioPanel(props); hooks.commit(); return tree; }
function find(node: unknown, key: string, value: string): Element | undefined {
  if (Array.isArray(node)) return node.map(child => find(child, key, value)).find(Boolean);
  if (!node || typeof node !== 'object' || !('props' in node)) return undefined;
  const element = node as Element;
  return element.props[key] === value ? element : find(element.props.children, key, value);
}
function deferredFile() {
  let resolve!: (value: string) => void, reject!: (reason: unknown) => void;
  const content = new Promise<string>((yes, no) => { resolve = yes; reject = no; });
  return { file: { size: 1024, text: () => content } as File, resolve, reject };
}
function select(file: File) {
  find(render(), 'aria-label', 'Import scenario or replay JSON')!.props.onChange({ target: { files: [file], value: '' } });
}
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
function replayJson() {
  const state = createDriverSimulation(withEventActors(demoScenario, props.plan));
  return JSON.stringify(createTrace({ ...state, events: createEvents(props.plan, 'baseline') }, 'fixture-import', 'test-fixture'));
}

beforeEach(() => {
  hooks.clear();
  props = { plan: preset(), onChange: vi.fn(), onReplay: vi.fn(), onBaseline: vi.fn(),
    driver: { active: false, trace: null, traces: [], hasRun: false, canStart: true, replaying: false } as unknown as GeminiDriver };
});
afterEach(() => hooks.unmount());

it('applies an import while its selection and component are still current', async () => {
  const pending = deferredFile(); select(pending.file);
  const imported = preset('stalled-vehicle');
  pending.resolve(JSON.stringify(imported)); await settle();
  expect(props.onChange).toHaveBeenCalledExactlyOnceWith(imported);
  expect(props.onReplay).not.toHaveBeenCalled();
});

it('applies a current replay and still reports a current invalid file', async () => {
  const replay = deferredFile(); select(replay.file);
  replay.resolve(replayJson()); await settle();
  expect(props.onReplay).toHaveBeenCalledTimes(1);
  const invalid = deferredFile(); select(invalid.file);
  invalid.resolve('{'); await settle();
  expect(find(render(), 'role', 'alert')).toBeDefined();
  expect(props.onReplay).toHaveBeenCalledTimes(1);
});

it('does not overwrite a newer preset selection with an older file', async () => {
  const pending = deferredFile(); select(pending.file);
  find(render(), 'aria-label', 'Scenario preset')!.props.onChange({ target: { value: 'stalled-vehicle' } });
  pending.resolve(JSON.stringify(preset())); await settle();
  expect(props.onChange).toHaveBeenCalledExactlyOnceWith(preset('stalled-vehicle'));
});

it('does not start a replay after the panel has unmounted', async () => {
  const pending = deferredFile(); select(pending.file);
  const json = replayJson(); hooks.unmount();
  pending.resolve(json); await settle();
  expect(props.onChange).not.toHaveBeenCalled();
  expect(props.onReplay).not.toHaveBeenCalled();
});

it('ignores an older file failure after a newer import succeeds', async () => {
  const old = deferredFile(), next = deferredFile();
  select(old.file); select(next.file);
  next.resolve(JSON.stringify(preset('stalled-vehicle'))); await settle();
  old.reject(new Error('old file failed')); await settle();
  expect(find(render(), 'role', 'alert')).toBeUndefined();
  expect(props.onChange).toHaveBeenCalledTimes(1);
});

it('invalidates a pending file when the parent changes the scenario plan', async () => {
  const pending = deferredFile(); select(pending.file);
  props = { ...props, plan: preset('stalled-vehicle') }; render();
  pending.resolve(JSON.stringify(preset())); await settle();
  expect(props.onChange).not.toHaveBeenCalled();
});

it('invalidates a pending replay when its parent replay context changes', async () => {
  const pending = deferredFile(); select(pending.file);
  const json = replayJson(), previous = props.onReplay;
  props = { ...props, onReplay: vi.fn() }; render();
  pending.resolve(json); await settle();
  expect(previous).not.toHaveBeenCalled();
  expect(props.onReplay).not.toHaveBeenCalled();
  expect(props.onChange).not.toHaveBeenCalled();
});

it('does not revive a file selection after a run starts and stops', async () => {
  const pending = deferredFile(); select(pending.file);
  props = { ...props, driver: { ...props.driver, active: true } }; render();
  props = { ...props, driver: { ...props.driver, active: false } }; render();
  pending.resolve(JSON.stringify(preset('stalled-vehicle'))); await settle();
  expect(props.onChange).not.toHaveBeenCalled();
});
