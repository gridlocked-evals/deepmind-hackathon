import { test, expect, type DriverHarness, type DecisionReply } from './fixtures';

const INVALID_DECISION = 'Gemini returned an invalid driving decision. The simulation has stopped.';

async function startPendingDrive(driver: DriverHarness) {
  await driver.open();
  await driver.startDriver();
  const request = await driver.gateway.waitForRequest(0);
  // Starting a run collapses the workspace sheet on mobile. Reopen it to
  // inspect the same visible status and controls on both viewport projects.
  await driver.openDriver();
  expect(request.step).toBe(0);
  await expect(driver.page.getByRole('status').filter({ hasText: 'Gemini is deciding' })).toBeVisible();
  await expect(driver.page.getByText('Simulation paused while the model responds.', { exact: true })).toBeVisible();
  expect(await driver.time()).toBe(0);
  expect(await driver.decisionCount()).toBe(0);
  return request;
}

async function expectStoppedWithoutFallback(driver: DriverHarness, message: string) {
  const { page } = driver;
  await expect(page.getByRole('alert')).toHaveText(message);
  await expect(page.getByRole('status').filter({ hasText: 'Drive interrupted' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Restart scenario', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Stop drive', exact: true })).toHaveCount(0);
  await expect(page.locator('.driver-history')).toHaveCount(0);
  expect(await driver.time()).toBe(0);
  expect(await driver.decisionCount()).toBe(0);

  // Exercise ordinary animation frames first, then pass a full minute without
  // replaying thousands of costly WebGL frames for a stopped scene.
  await page.clock.runFor(1_000);
  expect(await driver.time()).toBe(0);
  expect(await driver.decisionCount()).toBe(0);
  await page.clock.fastForward(59_000);
  expect(driver.gateway.calls).toHaveLength(1);
  expect(await driver.time()).toBe(0);
  expect(await driver.decisionCount()).toBe(0);
  await expect(page.getByRole('alert')).toHaveText(message);
  await expect(page.getByRole('status').filter({ hasText: 'Drive interrupted' })).toBeVisible();
}

test('HTTP 503 preserves the gateway error and never substitutes or retries a decision', async ({ driver }) => {
  await startPendingDrive(driver);
  await driver.page.clock.runFor(1_000);
  expect(await driver.time()).toBe(0);
  expect(driver.gateway.calls).toHaveLength(1);

  const message = 'Hydrogen gateway is temporarily unavailable for this driver run.';
  await driver.gateway.reply(0, { status: 503, raw: { detail: { message } } });
  await expectStoppedWithoutFallback(driver, message);
});

test('a pending decision freezes simulation until the 130-second timeout stops the drive', async ({ driver }) => {
  await startPendingDrive(driver);

  // The request must remain pending right up to the deadline, with no
  // simulated progress or replacement request.
  await driver.page.clock.fastForward(129_999);
  expect(await driver.time()).toBe(0);
  expect(await driver.decisionCount()).toBe(0);
  expect(driver.gateway.calls).toHaveLength(1);
  await expect(driver.page.getByRole('alert')).toHaveCount(0);
  await expect(driver.page.getByRole('button', { name: 'Stop drive', exact: true })).toBeVisible();

  await driver.page.clock.fastForward(1);
  await expectStoppedWithoutFallback(driver, 'The Gemini request timed out. Driving stopped without a fallback.');
});

test('invalid JSON is redacted and stops the run without fabricating a decision', async ({ driver }) => {
  await startPendingDrive(driver);
  const body = '<html>proxy error Authorization: Bearer browser-fixture-secret</html>';
  await driver.gateway.reply(0, { body });
  await expectStoppedWithoutFallback(driver, 'The driving request failed.');
  await expect(driver.page.locator('body')).not.toContainText('browser-fixture-secret');
  await expect(driver.page.locator('body')).not.toContainText('Authorization: Bearer');
});

const invalidResponses: { name: string; response: DecisionReply }[] = [
  { name: 'malformed decision', response: { decision: { target_speed_mps: -1 } } },
  { name: 'wrong run identity', response: { run_id: 'another-driver-run' } },
  { name: 'wrong step identity', response: { step: 1 } },
];

for (const { name, response } of invalidResponses) {
  test(`${name} stops the run without applying or retrying the response`, async ({ driver }) => {
    await startPendingDrive(driver);
    await driver.gateway.reply(0, response);
    await expectStoppedWithoutFallback(driver, INVALID_DECISION);
  });
}
