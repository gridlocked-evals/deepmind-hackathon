import { test, expect, FIXTURE_CAMERAS } from './fixtures';

test('camera and perspective changes survive basemap style replacement before its next frame', async ({ driver }) => {
  const { page } = driver;
  await driver.open({ delayedBasemap: true });
  const perspective = page.getByRole('navigation', { name: 'Map perspective' });
  await perspective.getByRole('button', { name: 'Intersection', exact: true }).click();
  await page.clock.runFor(64);
  const canvas = page.locator('.maplibregl-canvas');
  await expect(canvas).toHaveAttribute('data-scene-frame-time', '0');

  // The fallback is already ready. A new style replaces it synchronously but
  // MapLibre installs its layers on the next RAF, which the clock still holds.
  await driver.releaseBasemap();
  await expect(page.locator('.map-context-status')).toHaveCount(0);
  await perspective.getByRole('button', { name: 'Map', exact: true }).click();
  expect(driver.pageErrors).toEqual([]);

  await page.getByRole('button', { name: 'Open workspace tools', exact: true }).click();
  await page.locator('.rail-mode-switch').getByRole('button', { name: 'Cameras', exact: true }).click();
  await page.getByRole('textbox', { name: 'Search cameras', exact: true }).fill(FIXTURE_CAMERAS.short.name);
  await page.locator('.camera-row').filter({ hasText: FIXTURE_CAMERAS.short.name }).click();
  await driver.closeTools();
  await perspective.getByRole('button', { name: 'Follow', exact: true }).click();
  expect(driver.pageErrors).toEqual([]);

  // Let style.load install the latest view/scenario, then prove that the new
  // real renderer draws advancing driver frames rather than stale diagnostics.
  await page.clock.runFor(64);
  await driver.startDriver();
  await driver.gateway.waitForRequest(0);
  await driver.gateway.reply(0);
  await expect.poll(() => driver.decisionCount()).toBe(1);
  await page.clock.runFor(320);
  expect(await driver.time()).toBeGreaterThan(0);
  expect(Number(await canvas.getAttribute('data-scene-frame-time'))).toBeGreaterThan(0);
  await expect(canvas).toHaveAttribute('data-scene-ego-x', /^-?\d/);
  await expect(canvas).toHaveAttribute('data-scene-ego-y', /^-?\d/);
  await expect(perspective.getByRole('button', { name: 'Follow', exact: true })).toHaveAttribute('aria-pressed', 'true');
});
