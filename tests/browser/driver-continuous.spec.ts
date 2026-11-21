import { test, expect, type DriverHarness } from './fixtures';

const EXPIRED_COMMAND = 'The next driving decision did not arrive before the current command expired. Driving stopped.';

async function rendered(driver: DriverHarness) {
  const canvas = driver.page.locator('.maplibregl-canvas');
  await expect(canvas).toHaveAttribute('data-scene-frame-time', /^\d/);
  await expect(canvas).toHaveAttribute('data-scene-ego-x', /^-?\d/);
  await expect(canvas).toHaveAttribute('data-scene-ego-y', /^-?\d/);
  return canvas.evaluate(element => ({
    time: Number((element as HTMLCanvasElement).dataset.sceneFrameTime),
    x: Number((element as HTMLCanvasElement).dataset.sceneEgoX),
    y: Number((element as HTMLCanvasElement).dataset.sceneEgoY),
  }));
}

async function expectFinishedClock(driver: DriverHarness) {
  const time = await driver.time();
  const frame = await rendered(driver);
  const count = await driver.decisionCount();
  const requests = driver.gateway.calls.length;
  await driver.page.clock.fastForward(20_000);
  expect(await driver.time()).toBe(time);
  expect(await rendered(driver)).toEqual(frame);
  expect(await driver.decisionCount()).toBe(count);
  expect(driver.gateway.calls).toHaveLength(requests);
}

test('only the first decision pauses motion; a single pending refresh expires the original command after three seconds', async ({ driver }) => {
  await driver.open({ unabortableDecisions: true });
  await driver.planRoute('long');
  await driver.startDriver();
  const first = await driver.gateway.waitForRequest(0);
  await driver.page.clock.runFor(500);
  expect(await driver.time()).toBe(0);
  expect((await rendered(driver)).time).toBe(0);
  expect(await driver.decisionCount()).toBe(0);
  expect(driver.gateway.calls).toHaveLength(1);
  await driver.openDriver();
  await expect(driver.page.locator('.driver-decision-heading')).toHaveText('Gemini is deciding');

  await driver.gateway.reply(0);
  await expect.poll(() => driver.decisionCount()).toBe(1);
  await driver.page.clock.runFor(600);
  const next = await driver.gateway.waitForRequest(1);
  expect(next.run_id).toBe(first.run_id);
  expect(next.step).toBe(1);
  const before = await rendered(driver);
  await driver.page.clock.runFor(1000);
  const moving = await rendered(driver);
  expect(moving.time).toBeGreaterThan(before.time);
  expect(moving.y).toBeGreaterThan(before.y);
  expect(await driver.time()).toBeGreaterThan(1);
  expect(await driver.decisionCount()).toBe(1);
  expect(driver.gateway.calls).toHaveLength(2);
  await expect(driver.page.locator('.driver-decision-heading')).toHaveText('Driving the route');

  // Freshness starts when the observation was captured, including the initial
  // 500ms response delay. It must not restart when that command was applied.
  await driver.page.clock.runFor(899);
  await expect(driver.page.getByRole('alert')).toHaveCount(0);
  expect(driver.gateway.calls).toHaveLength(2);
  await driver.page.clock.runFor(1);
  await expect(driver.page.getByRole('alert')).toHaveText(EXPIRED_COMMAND);
  await expect(driver.page.locator('.driver-decision-heading')).toHaveText('Drive interrupted');
  expect(await driver.time()).toBeLessThanOrEqual(2.5);
  expect(await driver.decisionCount()).toBe(1);

  // Even a transport that ignores the abort cannot revive the expired run.
  await driver.gateway.reply(1, { decision: { reason: 'EXPIRED REFRESH RESPONSE' } });
  await driver.page.clock.runFor(32);
  await expectFinishedClock(driver);
  await expect(driver.page.locator('.driver-panel')).not.toContainText('EXPIRED REFRESH RESPONSE');
  expect(await driver.decisionSignals()).toEqual([true, true]);
});

for (const budget of ['time', 'decisions'] as const) {
  test(`continuous refreshes stop at the ${budget === 'time' ? '60-second' : '60-decision'} budget without another request`, async ({ driver }) => {
    test.setTimeout(300_000);
    await driver.open();
    await driver.planRoute('long');
    await driver.startDriver();
    await driver.closeTools();
    const perspective = driver.page.getByRole('navigation', { name: 'Map perspective' });
    // This budget test still runs every native animation callback. City view
    // avoids spending a minute rasterizing cars; other cases inspect motion.
    await perspective.getByRole('button', { name: 'Map', exact: true }).click();
    await driver.gateway.waitForRequest(0);
    if (budget === 'decisions') await driver.page.clock.runFor(1000);

    let runId: string | undefined;
    let previousTime = 0;
    let finalAppliedAt = 0;
    for (let step = 0; step < 60; step++) {
      const request = await driver.gateway.waitForRequest(step);
      runId ??= request.run_id;
      expect(request.run_id).toBe(runId);
      expect(request.step).toBe(step);
      expect(request.observation.simulation_time).toBeGreaterThanOrEqual(previousTime);
      expect(request.observation.simulation_time - previousTime).toBeLessThanOrEqual(1.100001);
      expect(request.observation.simulation_time).toBeLessThan(60);
      expect(driver.gateway.calls).toHaveLength(step + 1);
      previousTime = request.observation.simulation_time;
      if (step === 59) finalAppliedAt = await driver.time();
      await driver.gateway.reply(step);
      await expect.poll(() => driver.decisionCount()).toBe(step + 1);
      // Delaying only the first response gives the decision budget a head
      // start over simulated time; its next request is already due at 1s.
      await driver.page.clock.runFor(step === 0 && budget === 'decisions' ? 16 : 1008);
    }

    await driver.openDriver();
    await expect(driver.page.locator('.driver-decision-heading')).toHaveText('Demo complete');
    await expect(driver.page.locator('.driver-decision')).toContainText(budget === 'time'
      ? 'The 60-second drive is complete.' : 'The 60-decision limit is reached.');
    expect(await driver.decisionCount()).toBe(60);
    expect(driver.gateway.calls).toHaveLength(60);
    if (budget === 'time') expect(await driver.time()).toBe(60);
    else {
      expect(await driver.time()).toBeGreaterThan(finalAppliedAt + 0.8);
      expect(await driver.time()).toBeLessThan(60);
    }

    await driver.closeTools();
    await perspective.getByRole('button', { name: 'Follow', exact: true }).click();
    await driver.page.clock.runFor(64);
    const frame = await rendered(driver);
    expect(frame.time).toBeLessThanOrEqual(60.000001);
    expect(Math.abs(frame.time - await driver.time())).toBeLessThan(0.11);
    await expectFinishedClock(driver);
  });
}

test('sixty simulated seconds cancel a pending final decision and keep its late response inert', async ({ driver }) => {
  test.setTimeout(300_000);
  await driver.open({ unabortableDecisions: true });
  await driver.planRoute('long');
  await driver.startDriver();
  await driver.closeTools();
  const perspective = driver.page.getByRole('navigation', { name: 'Map perspective' });
  await perspective.getByRole('button', { name: 'Map', exact: true }).click();
  const first = await driver.gateway.waitForRequest(0);
  await driver.gateway.reply(0);
  await expect.poll(() => driver.decisionCount()).toBe(1);
  await driver.page.clock.runFor(1008);
  for (let step = 1; step < 60; step++) {
    const request = await driver.gateway.waitForRequest(step);
    expect(request.run_id).toBe(first.run_id);
    expect(request.step).toBe(step);
    expect(driver.gateway.calls).toHaveLength(step + 1);
    // Keep inference pending while motion advances, including at the deadline.
    await driver.page.clock.runFor(700);
    if (await driver.time() === 60) break;
    await driver.gateway.reply(step);
    await expect.poll(() => driver.decisionCount()).toBe(step + 1);
    await driver.page.clock.runFor(308);
  }
  await driver.openDriver();
  await expect(driver.page.locator('.driver-decision-heading')).toHaveText('Demo complete');
  await expect(driver.page.locator('.driver-decision')).toContainText('The 60-second drive is complete.');
  expect(await driver.time()).toBe(60);
  expect(driver.gateway.calls).toHaveLength(60);
  expect(await driver.decisionCount()).toBe(59);

  await driver.closeTools();
  await perspective.getByRole('button', { name: 'Follow', exact: true }).click();
  await driver.page.clock.runFor(64);
  expect((await rendered(driver)).time).toBeCloseTo(60, 6);
  await driver.gateway.reply(59, { decision: { reason: 'TOO LATE AT TIME LIMIT' } });
  await expectFinishedClock(driver);
  expect(await driver.decisionCount()).toBe(59);
  await driver.openDriver();
  await expect(driver.page.locator('.driver-panel')).not.toContainText('TOO LATE AT TIME LIMIT');
});

test('continuous driving reaches a short route destination before either run budget', async ({ driver }) => {
  await driver.open();
  await driver.planRoute('short');
  await driver.startDriver();
  for (let step = 0; step < 10; step++) {
    await driver.gateway.waitForRequest(step);
    await driver.gateway.reply(step);
    await expect.poll(() => driver.decisionCount()).toBe(step + 1);
    await driver.page.clock.runFor(1008);
    await driver.openDriver();
    if (await driver.page.locator('.driver-decision-heading').innerText() === 'Destination reached') break;
  }
  await expect(driver.page.locator('.driver-decision-heading')).toHaveText('Destination reached');
  expect(driver.gateway.calls.length).toBeLessThan(60);
  expect(await driver.time()).toBeLessThan(60);
  await expect(driver.page.locator('.driver-metrics > div').nth(1)).toContainText('0m');
  await expect(driver.page.locator('.driver-decision')).not.toContainText('limit is reached');
  await expectFinishedClock(driver);
});
