import { test, expect, FIXTURE_CAMERAS, type DriverHarness } from './fixtures';

async function rendered(driver: DriverHarness) {
  const canvas = driver.page.locator('.maplibregl-canvas');
  await expect(canvas).toHaveAttribute('data-scene-frame-time', /\d/);
  await expect(canvas).toHaveAttribute('data-scene-ego-x', /^-?\d/);
  await expect(canvas).toHaveAttribute('data-scene-ego-y', /^-?\d/);
  return canvas.evaluate(element => {
    const data = (element as HTMLCanvasElement).dataset;
    return { scenario: data.sceneScenarioId, time: Number(data.sceneFrameTime),
      x: Number(data.sceneEgoX), y: Number(data.sceneEgoY) };
  });
}

async function expectFrozen(driver: DriverHarness, milliseconds = 20_000) {
  const time = await driver.time();
  const frame = await rendered(driver);
  const requests = driver.gateway.calls.length;
  // No physics is running here; jumping wall time must not move the rendered car.
  await driver.page.clock.fastForward(milliseconds);
  expect(await driver.time()).toBe(time);
  expect(await rendered(driver)).toEqual(frame);
  expect(driver.gateway.calls).toHaveLength(requests);
}

test('stopping while thinking or driving freezes the car; restart owns a new run', async ({ driver }) => {
  await driver.open();
  await driver.planRoute('long');
  await driver.startDriver();
  const first = await driver.gateway.waitForRequest(0);
  await driver.page.clock.runFor(64);
  await driver.closeTools();
  await driver.page.getByRole('button', { name: 'Stop Gemini drive', exact: true }).click();
  await expectFrozen(driver);
  await driver.openDriver();
  await expect(driver.page.getByRole('status').filter({ hasText: 'Drive stopped' })).toBeVisible();
  expect(await driver.decisionCount()).toBe(0);

  await driver.startDriver();
  const second = await driver.gateway.waitForRequest(1);
  expect(second.run_id).not.toBe(first.run_id);
  expect(second.step).toBe(0);
  expect(second.observation.simulation_time).toBe(0);
  await driver.gateway.reply(1);
  await expect.poll(() => driver.decisionCount()).toBe(1);
  await driver.page.clock.runFor(1200);
  expect(await driver.time()).toBeGreaterThan(1);
  const moving = await rendered(driver);
  expect(Math.hypot(moving.x, moving.y)).toBeGreaterThan(0.1);
  const canvas = driver.page.locator('.maplibregl-canvas');
  await expect(canvas).toHaveAttribute('data-scene-gl-error', '0');
  expect(Number(await canvas.getAttribute('data-scene-draw-calls'))).toBeGreaterThan(0);
  expect(Number(await canvas.getAttribute('data-scene-triangles'))).toBeGreaterThan(0);
  await driver.openDriver();
  await driver.page.getByRole('button', { name: 'Stop drive', exact: true }).click();
  await driver.page.clock.runFor(32);
  await expectFrozen(driver);
  await expect(driver.page.getByRole('button', { name: 'Restart scenario' })).toBeEnabled();
});

test('late responses remain inert after Stop and after a newer run starts, even when abort is ignored', async ({ driver }) => {
  await driver.open({ unabortableDecisions: true });
  await driver.planRoute('long');
  await driver.startDriver();
  await driver.gateway.waitForRequest(0);
  await driver.page.clock.runFor(32);
  await driver.closeTools();
  await driver.page.getByRole('button', { name: 'Stop Gemini drive', exact: true }).click();
  await driver.gateway.reply(0, { decision: { reason: 'CANCELLED FIRST RESPONSE' } });
  await expectFrozen(driver);
  expect(await driver.decisionCount()).toBe(0);
  await driver.openDriver();
  await expect(driver.page.locator('.driver-decision-heading')).toHaveText('Drive stopped');

  await driver.startDriver();
  const old = await driver.gateway.waitForRequest(1);
  await driver.closeTools();
  await driver.page.getByRole('button', { name: 'Stop Gemini drive', exact: true }).click();
  await driver.startDriver();
  const current = await driver.gateway.waitForRequest(2);
  expect(current.run_id).not.toBe(old.run_id);
  await driver.gateway.reply(2, { decision: { reason: 'CURRENT RUN RESPONSE' } });
  await expect.poll(() => driver.decisionCount()).toBe(1);
  await driver.page.clock.runFor(1100);
  const before = await rendered(driver);
  await driver.gateway.reply(1, { decision: { action: 'stop', target_speed_mps: 0, reason: 'CANCELLED SECOND RESPONSE' } });
  await driver.page.clock.runFor(1000);
  const after = await rendered(driver);
  expect(after.time).toBeGreaterThan(before.time);
  expect(after.y).toBeGreaterThan(before.y);
  expect(await driver.decisionCount()).toBe(1);
  expect(driver.gateway.calls).toHaveLength(4);
  expect(driver.gateway.calls[3].run_id).toBe(current.run_id);
  expect(driver.gateway.calls[3].step).toBe(1);
  expect(await driver.decisionSignals()).toEqual([true, true, true, true]);
  await driver.openDriver();
  await expect(driver.page.locator('.driver-decision-heading')).toHaveText('Driving the route');
  await expect(driver.page.locator('.driver-decision')).toContainText('CURRENT RUN RESPONSE');
  await expect(driver.page.locator('.driver-panel')).not.toContainText('CANCELLED');
});

for (const change of ['camera', 'route', 'demo'] as const) {
  test(`${change} changes cancel a pending run before replacing the scene`, async ({ driver }) => {
    await driver.open({ unabortableDecisions: true });
    await driver.planRoute('long');
    await driver.startDriver();
    const old = await driver.gateway.waitForRequest(0);
    await driver.closeTools();
    if (change === 'camera') {
      await driver.page.getByRole('button', { name: 'Open workspace tools', exact: true }).click();
      await driver.page.locator('.rail-mode-switch').getByRole('button', { name: 'Cameras', exact: true }).click();
      await driver.page.getByRole('textbox', { name: 'Search cameras', exact: true }).fill(FIXTURE_CAMERAS.short.name);
      await driver.page.locator('.camera-row').filter({ hasText: FIXTURE_CAMERAS.short.name }).click();
    } else if (change === 'route') {
      await driver.planRoute('short');
    } else {
      await driver.page.getByRole('button', { name: 'Traffic demo', exact: true }).click();
      await driver.page.getByRole('button', { name: 'Pause simulation', exact: true }).click();
    }
    await driver.gateway.reply(0, { decision: { reason: 'ABANDONED SCENE RESPONSE' } });
    await driver.page.clock.runFor(100);
    await driver.openDriver();
    await expect(driver.page.locator('.driver-decision-heading')).toHaveText(change === 'camera' ? 'Drive stopped' : 'Ready for a driver');
    expect(await driver.decisionCount()).toBe(0);
    await expect(driver.page.locator('.driver-panel')).not.toContainText('ABANDONED SCENE RESPONSE');
    expect(driver.gateway.calls).toHaveLength(1);

    await driver.startDriver();
    const next = await driver.gateway.waitForRequest(1);
    expect(next.run_id).not.toBe(old.run_id);
    expect(next.step).toBe(0);
    if (change !== 'camera') expect(next.observation.scenario_id).not.toBe(old.observation.scenario_id);
    await driver.page.clock.runFor(64);
    expect((await rendered(driver)).scenario).toBe(next.observation.scenario_id);
    await expectFrozen(driver, 500);
  });
}

for (const phase of ['thinking', 'driving'] as const) {
  test(`visibility API hidden event stops a ${phase} run and visibility return never resumes it`, async ({ driver }) => {
    await driver.open({ unabortableDecisions: true });
    await driver.planRoute('long');
    await driver.startDriver();
    await driver.gateway.waitForRequest(0);
    if (phase === 'driving') {
      await driver.gateway.reply(0);
      await expect.poll(() => driver.decisionCount()).toBe(1);
      await driver.page.clock.runFor(1100);
    } else await driver.page.clock.runFor(64);
    // Headless Chromium keeps background pages visible. Inject the browser API
    // contract explicitly; this is not a claim to test OS tab backgrounding.
    await driver.page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(await driver.page.evaluate(() => document.hidden)).toBe(true);
    if (phase === 'thinking') await driver.gateway.reply(0);
    await driver.page.clock.runFor(32);
    await expectFrozen(driver);
    const stoppedTime = await driver.time();
    await driver.page.evaluate(() => {
      Reflect.deleteProperty(document, 'hidden');
      Reflect.deleteProperty(document, 'visibilityState');
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(await driver.page.evaluate(() => document.visibilityState)).toBe('visible');
    await driver.page.clock.runFor(32);
    expect(await driver.time()).toBe(stoppedTime);
    expect(Math.abs((await rendered(driver)).time - stoppedTime)).toBeLessThan(0.12);
    await expectFrozen(driver);
    await driver.openDriver();
    await expect(driver.page.locator('.driver-decision-heading')).toHaveText('Drive stopped');
    await expect(driver.page.getByRole('button', { name: 'Restart scenario' })).toBeEnabled();
  });
}

test('replay, driver, and perspective changes share the frame consumed by the real renderer', async ({ driver }) => {
  await driver.open();
  await driver.planRoute('long');
  const timeline = driver.page.getByRole('slider', { name: 'Simulation timeline' });
  await timeline.press('End');
  await driver.page.clock.runFor(64);
  expect(await driver.time()).toBeGreaterThan(30);
  const replay = await rendered(driver);
  await driver.page.getByRole('button', { name: 'Play simulation', exact: true }).click();
  await driver.startDriver();
  await driver.gateway.waitForRequest(0);
  await driver.page.clock.runFor(64);
  await expect(timeline).toBeDisabled();
  expect(await driver.time()).toBe(0);
  expect((await rendered(driver)).time).toBe(0);
  expect((await rendered(driver)).y).not.toBe(replay.y);
  await expectFrozen(driver, 500);
  await driver.gateway.reply(0);
  await expect.poll(() => driver.decisionCount()).toBe(1);
  await driver.page.clock.runFor(1100);
  await driver.closeTools();
  for (const perspective of ['Intersection', 'Map', 'Follow']) {
    await driver.page.getByRole('navigation', { name: 'Map perspective' }).getByRole('button', { name: perspective, exact: true }).click();
    await driver.page.clock.runFor(320);
    await expect(driver.page.getByRole('button', { name: perspective, exact: true })).toHaveAttribute('aria-pressed', 'true');
    // City overview deliberately culls 3D cars. Check consumed frames at street zoom.
    if (perspective !== 'Map') expect(Math.abs((await rendered(driver)).time - await driver.time())).toBeLessThan(0.12);
  }
  expect(driver.gateway.calls).toHaveLength(2);
  await driver.page.getByRole('button', { name: 'Return to replay', exact: true }).click();
  await driver.page.clock.runFor(64);
  await expect(timeline).toBeEnabled();
  expect(await driver.time()).toBe(0);
  expect((await rendered(driver)).time).toBe(0);
  await expectFrozen(driver);
  await driver.page.getByRole('button', { name: 'Play simulation', exact: true }).click();
  await driver.page.clock.runFor(1100);
  expect(await driver.time()).toBeGreaterThan(1);
  expect(Math.abs((await rendered(driver)).time - await driver.time())).toBeLessThan(0.12);
});
