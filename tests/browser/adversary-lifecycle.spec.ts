import { test, expect, type DriverHarness } from './fixtures';
import type { AttackDecision } from '../../src/scenarios/adversary';

const attack: AttackDecision = { action: 'attack', event_type: 'stalled-vehicle', trigger_seconds: 0,
  parameters: { distanceAheadM: 80, approachSeconds: 1 }, durationSeconds: 20, reason: 'Offline Carbon fixture.' };

async function configure(driver: DriverHarness, mode: 'carbon' | 'random') {
  await driver.planRoute('long');
  await driver.openDriver();
  await driver.page.getByLabel('Adversary', { exact: true }).selectOption(mode);
}

async function startCarbon(driver: DriverHarness) {
  await configure(driver, 'carbon');
  await driver.startDriver('event');
  const driving = await driver.gateway.waitForRequest(0);
  await driver.page.clock.runFor(32);
  const carbon = await driver.adversary.waitForRequest(0);
  expect(carbon.run_id).toBe(driving.run_id);
  expect(carbon.state.simulation_time).toBe(0);
  return driving;
}

test('Carbon pauses motion and command age, then its hazard reaches the next driver observation', async ({ driver }) => {
  await driver.open();
  await startCarbon(driver);
  await driver.gateway.reply(0);
  await expect.poll(() => driver.decisionCount()).toBe(1);
  await driver.page.clock.fastForward(5000); // A barrier freezes physics; no driving frames are skipped.
  expect(await driver.time()).toBe(0);
  expect(driver.gateway.calls).toHaveLength(1);
  expect(driver.adversary.calls).toHaveLength(1);
  await driver.openDriver();
  await expect(driver.page.getByRole('alert')).toHaveCount(0);
  await expect(driver.page.getByLabel('Adversary', { exact: true })).toBeDisabled();
  await driver.adversary.reply(0, { decision: attack });
  await driver.page.clock.runFor(1200);
  const next = await driver.gateway.waitForRequest(1);
  expect(next.observation.lead_gap_m).not.toBeNull();
  expect(next.observation.lead_gap_m!).toBeLessThan(80);
  expect(JSON.stringify(next.observation)).not.toMatch(/attack|Carbon|trigger|stalled/);
  expect(await driver.time()).toBeGreaterThan(1);
  await expect(driver.page.getByLabel('Attacks')).toContainText('applied 0.0s');
});

test('Stop and restart reject late Carbon and driver responses from the abandoned run', async ({ driver }) => {
  await driver.open({ unabortableDecisions: true, unabortableAdversary: true });
  const old = await startCarbon(driver);
  await driver.openDriver();
  await driver.page.getByRole('button', { name: 'Stop drive', exact: true }).click();
  await driver.page.clock.fastForward(5000);
  expect(await driver.time()).toBe(0);
  await driver.startDriver('event');
  const fresh = await driver.gateway.waitForRequest(1);
  await driver.page.clock.runFor(32);
  const carbon = await driver.adversary.waitForRequest(1);
  expect(fresh.run_id).not.toBe(old.run_id);
  expect(carbon.run_id).toBe(fresh.run_id);
  await driver.adversary.reply(1);
  await driver.gateway.reply(1, { decision: { reason: 'CURRENT DRIVER' } });
  await expect.poll(() => driver.decisionCount()).toBe(1);
  await driver.adversary.reply(0, { decision: { ...attack, reason: 'STALE CARBON ATTACK' } });
  await driver.gateway.reply(0, { decision: { reason: 'STALE DRIVER' } });
  await driver.page.clock.runFor(1200);
  expect(await driver.time()).toBeGreaterThan(1);
  const next = await driver.gateway.waitForRequest(2);
  expect(next.observation.lead_gap_m).toBeNull();
  await driver.openDriver();
  await expect(driver.page.getByLabel('Attacks')).toHaveCount(0);
  await expect(driver.page.locator('.driver-panel')).not.toContainText('STALE');
  await expect(driver.page.locator('.driver-decision')).toContainText('CURRENT DRIVER');
});

test('Carbon errors stop only the adversary, with no fallback attack or repeated request', async ({ driver }) => {
  await driver.open();
  await startCarbon(driver);
  await driver.adversary.reply(0, { status: 503, raw: { detail: { message: 'Offline Carbon failure' } } });
  for (let step = 0; step < 4; step++) {
    await driver.gateway.reply(step);
    await expect.poll(() => driver.decisionCount()).toBe(step + 1);
    await driver.page.clock.runFor(1008);
  }
  await driver.openDriver();
  await expect(driver.page.locator('.adversary-lane [role=alert]')).toContainText('No fallback attack');
  await expect(driver.page.locator('.driver-decision-heading')).toHaveText('Driving the route');
  await expect(driver.page.getByLabel('Attacks')).toHaveCount(0);
  expect(await driver.time()).toBeGreaterThan(3);
  expect(driver.adversary.calls).toHaveLength(1);
});

test('the seeded random lane applies an attack without any Carbon request', async ({ driver }) => {
  await driver.open();
  await configure(driver, 'random');
  await driver.page.getByLabel('Random adversary seed').fill('37');
  await driver.startDriver('event');
  for (let step = 0; step < 4; step++) {
    await driver.gateway.reply(step);
    await expect.poll(() => driver.decisionCount()).toBe(step + 1);
    await driver.page.clock.runFor(1008);
  }
  const observation = (await driver.gateway.waitForRequest(4)).observation;
  expect(observation.lead_gap_m).not.toBeNull();
  expect(observation.lead_gap_m!).toBeGreaterThan(0);
  expect(driver.adversary.calls).toHaveLength(0);
  await driver.openDriver();
  await expect(driver.page.getByLabel('Attacks')).toContainText('random');
  await expect(driver.page.getByLabel('Attacks')).toContainText('applied 3.0s');
});


test('adversary runs disable unrelated manual injection and Off restores the Scenario Lab plan', async ({ driver }) => {
  await driver.open();
  await driver.planRoute('long');
  await driver.openDriver();
  await driver.page.getByLabel('Event trigger').selectOption('manual');
  await driver.page.getByLabel('Adversary', { exact: true }).selectOption('carbon');
  await driver.startDriver('event');
  await driver.gateway.waitForRequest(0);
  await driver.page.clock.runFor(32);
  await driver.adversary.waitForRequest(0);
  await driver.openDriver();
  const inject = driver.page.getByRole('button', { name: 'Inject event', exact: true });
  await expect(inject).toBeDisabled();
  await driver.page.getByRole('button', { name: 'Stop drive', exact: true }).click();
  await driver.page.getByLabel('Adversary', { exact: true }).selectOption('off');
  await driver.startDriver('event');
  await driver.gateway.waitForRequest(1);
  await driver.openDriver();
  await expect(inject).toBeEnabled();
  await inject.click();
  await driver.gateway.reply(1);
  await expect.poll(() => driver.decisionCount()).toBe(1);
  await driver.page.clock.runFor(1100);
  const observed = await driver.gateway.waitForRequest(2);
  expect(observed.observation.lead_gap_m).not.toBeNull();
  expect(driver.adversary.calls).toHaveLength(1);
});
