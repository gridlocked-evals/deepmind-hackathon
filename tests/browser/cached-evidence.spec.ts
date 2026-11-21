import { test, expect, FIXTURE_CAMERAS, type DriverHarness } from './fixtures';
import { EVIDENCE_TOKEN } from './evidence-fixtures';

async function camera(driver: DriverHarness, kind: 'start' | 'short') {
  const close = driver.page.getByRole('button', { name: 'Close evidence panel', exact: true });
  if (await close.isVisible()) await close.click();
  if (!await driver.page.locator('.source-rail').isVisible()) await driver.page.getByRole('button', { name: 'Open workspace tools', exact: true }).click();
  await driver.page.locator('.rail-mode-switch').getByRole('button', { name: 'Cameras', exact: true }).click();
  await driver.page.getByRole('textbox', { name: 'Search cameras', exact: true }).fill(FIXTURE_CAMERAS[kind].name);
  await driver.page.locator('.camera-row').filter({ hasText: FIXTURE_CAMERAS[kind].name }).click();
  await driver.closeTools();
}
async function openEvidence(driver: DriverHarness) {
  await driver.page.getByRole('button', { name: 'Camera evidence', exact: true }).click();
  await expect(driver.page.getByRole('heading', { name: 'Evidence to possibility', exact: true })).toBeVisible();
}
async function token(driver: DriverHarness, value = EVIDENCE_TOKEN) {
  const input = driver.page.getByLabel('Operator token', { exact: true });
  if (!await input.isVisible()) await driver.page.locator('.evidence-operator > summary').click();
  await input.fill(value);
}
async function loadReport(driver: DriverHarness) {
  await camera(driver, 'start'); await openEvidence(driver);
  await driver.page.getByRole('button', { name: /^Load cached clip/ }).click();
  await token(driver);
  await driver.page.getByRole('button', { name: 'Load cached observations · no model call', exact: true }).click();
  await expect(driver.page.locator('.evidence-result-summary')).toContainText('Extraction completed');
}

test('cached footage stays public while retained reports require an authenticated GET, without paid fallback', async ({ driver }) => {
  driver.evidence.add(FIXTURE_CAMERAS.start, 'clear', 0, true);
  await driver.open(); await camera(driver, 'start'); await openEvidence(driver);
  await expect(driver.page.getByRole('heading', { name: 'Cached for the demo', exact: true })).toBeVisible();
  await driver.page.getByRole('button', { name: /^Load cached clip/ }).click();
  await expect(driver.page.getByRole('heading', { name: 'Retained original', exact: true })).toBeVisible();
  await expect(driver.page.locator('.evidence-panel')).not.toContainText('UNAUTHORIZED EMBEDDED REPORT');
  await expect(driver.page.getByRole('button', { name: 'Drive here with this evidence', exact: true })).toHaveCount(0);
  const retained = driver.page.getByRole('button', { name: 'Load cached observations · no model call', exact: true });
  await expect(retained).toBeDisabled();
  expect(driver.evidence.reportCalls).toHaveLength(0);
  await driver.page.getByRole('button', { name: 'Review original media', exact: true }).click();
  await expect.poll(() => driver.evidence.mediaCalls).toContain('/api/captures/cap_browser-start/index.m3u8');
  await token(driver, 'incorrect-offline-operator-token-000001');
  await retained.click();
  await expect(driver.page.locator('.evidence-error[role=alert]')).toContainText('Enter the operator token');
  await expect(driver.page.getByRole('heading', { name: 'Retained original', exact: true })).toBeVisible();
  await token(driver); await retained.click();
  await expect(driver.page.locator('.evidence-result-summary')).toContainText('Extraction completed');
  expect(driver.evidence.reportCalls).toHaveLength(2);
  expect(driver.evidence.reportCalls[1]).toMatchObject({ method: 'GET', authorization: `Bearer ${EVIDENCE_TOKEN}` });
  expect(driver.evidence.reportCalls.every(call => !call.url.includes('token') && !call.url.includes(EVIDENCE_TOKEN))).toBe(true);
  expect(driver.gateway.calls).toHaveLength(0);
});

test('a retained report for another capture is rejected without exposing its facts', async ({ driver }) => {
  const report = driver.evidence.add(FIXTURE_CAMERAS.start);
  driver.evidence.reportOverride = { ...report, captureId: 'cap_unrelated' };
  await driver.open(); await camera(driver, 'start'); await openEvidence(driver);
  await driver.page.getByRole('button', { name: /^Load cached clip/ }).click();
  await token(driver);
  await driver.page.getByRole('button', { name: 'Load cached observations · no model call', exact: true }).click();
  await expect(driver.page.locator('.evidence-error[role=alert]')).toContainText('does not match this saved source evidence');
  await expect(driver.page.locator('.evidence-result-summary')).toHaveCount(0);
  await expect(driver.page.getByRole('button', { name: 'Drive here with this evidence', exact: true })).toHaveCount(0);
});

for (const assessment of ['clear', 'unknown'] as const) {
  test(`accepted ${assessment} obstruction evidence preserves the authored scenario plan`, async ({ driver }) => {
    driver.evidence.add(FIXTURE_CAMERAS.start, assessment);
    await driver.open(); await loadReport(driver);
    await expect(driver.page.locator('.evidence-drive')).not.toContainText('stalled-vehicle hazard');
    await driver.page.getByRole('button', { name: 'Drive here with this evidence', exact: true }).click();
    await driver.planRoute('long'); await driver.openDriver();
    await expect(driver.page.getByLabel('Scenario preset')).toHaveValue('car-crash');
    await driver.startDriver('event');
    const request = await driver.gateway.waitForRequest(0);
    expect(request.observation.lead_gap_m).toBeNull();
    expect(request.observation.route_label).not.toContain('traffic count from camera evidence');
  });
}

test('blocked evidence stays scoped to its camera while adversary controls remain usable', async ({ driver }) => {
  driver.evidence.add(FIXTURE_CAMERAS.start, 'blocked', 3);
  await driver.open(); await loadReport(driver);
  await expect(driver.page.locator('.evidence-drive')).toContainText('Adds 3 camera-counted vehicles and a stalled-vehicle hazard');
  await driver.page.getByRole('button', { name: 'Drive here with this evidence', exact: true }).click();
  await driver.planRoute('long'); await driver.openDriver();
  await expect(driver.page.getByLabel('Scenario preset')).toHaveValue('stalled-vehicle');
  await driver.page.getByLabel('Adversary', { exact: true }).selectOption('carbon');
  await driver.startDriver('event');
  const local = await driver.gateway.waitForRequest(0);
  expect(local.observation.lead_gap_m).toBeGreaterThan(0);
  expect(local.observation.lead_gap_m!).toBeLessThan(35);
  expect(local.observation.lead_speed_mps).toBeGreaterThan(0);
  expect(local.observation.lead_speed_mps).toBeLessThanOrEqual(50);
  await driver.page.clock.runFor(32); await driver.adversary.waitForRequest(0);
  await driver.openDriver(); await driver.page.getByRole('button', { name: 'Stop drive', exact: true }).click();
  await camera(driver, 'short'); await driver.planRoute('long'); await driver.openDriver();
  await expect(driver.page.getByLabel('Scenario preset')).toHaveValue('car-crash');
  await expect(driver.page.getByLabel('Adversary', { exact: true })).toHaveValue('carbon');
  await driver.startDriver('event');
  const elsewhere = await driver.gateway.waitForRequest(1);
  expect(elsewhere.observation.lead_gap_m).toBeNull();
  expect(elsewhere.observation.route_label).not.toContain('traffic count from camera evidence');
});

test('a delayed protected report stays with its camera when the operator changes views', async ({ driver }) => {
  driver.evidence.add(FIXTURE_CAMERAS.start, 'blocked', 3);
  driver.evidence.add(FIXTURE_CAMERAS.short, 'clear');
  driver.evidence.holdReports = true;
  await driver.open(); await camera(driver, 'start'); await openEvidence(driver);
  await driver.page.getByRole('button', { name: /^Load cached clip/ }).click();
  await token(driver); await driver.page.getByRole('button', { name: 'Load cached observations · no model call', exact: true }).click();
  await expect.poll(() => driver.evidence.reportCalls.length).toBe(1);
  await camera(driver, 'short'); await openEvidence(driver);
  await driver.evidence.replyReport(0);
  await expect(driver.page.locator('.evidence-result-summary')).toHaveCount(0);
  await camera(driver, 'start'); await openEvidence(driver);
  await expect(driver.page.locator('.evidence-result-summary')).toContainText('extract_browser-start');
  await expect(driver.page.locator('.evidence-drive')).toContainText('Adds 3 camera-counted vehicles');
  expect(driver.evidence.reportCalls).toHaveLength(1);
});
