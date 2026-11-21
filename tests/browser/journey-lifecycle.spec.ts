import { readFile } from 'node:fs/promises';
import { test, expect, FIXTURE_CAMERAS, type DriverHarness } from './fixtures';
import { EVIDENCE_TOKEN } from './evidence-fixtures';
import { NYC_STOPS } from '../../src/journey/route';
import type { RunTrace } from '../../src/scenarios/trace';

async function openJourney(driver: DriverHarness) {
  const known = new Set(NYC_STOPS.map(point => point.coordinates.map(value => value.toFixed(6)).join(',')));
  await driver.page.route('https://router.project-osrm.org/route/v1/driving/**', async route => {
    const url = new URL(route.request().url());
    const points = url.pathname.split('/').at(-1)!.split(';');
    if (points.length !== 2 || !points.every(point => known.has(point))) { await route.fallback(); return; }
    expect(route.request().method()).toBe('GET');
    expect(url.searchParams.get('geometries')).toBe('geojson');
    driver.routeRequests.push(url.href);
    await route.fulfill({ headers: { 'access-control-allow-origin': '*' }, json: {
      code: 'Ok', waypoints: [{ distance: 0 }, { distance: 0 }], routes: [{
        distance: 2000, duration: 250,
        geometry: { type: 'LineString', coordinates: points.map(point => point.split(',').map(Number)) },
        legs: [{ steps: [{ name: 'Offline journey street', distance: 2000, maneuver: { type: 'depart' } }] }],
      }],
    } });
  });
  await driver.open({ unabortableDecisions: true, unabortableAdversary: true });
  await driver.page.getByRole('button', { name: 'NYC journey', exact: true }).click();
}

async function startOpening(driver: DriverHarness) {
  await driver.page.getByRole('button', { name: /^(Start journey|Start new journey)$/ }).click();
  await expect(driver.page.locator('.journey-status')).toHaveText('Scripted opening in progress');
  await expect(driver.page.locator('.journey-opening')).toContainText('Preset hazards, steering and speed');
  await expect(driver.page.locator('.journey-panel')).toContainText('The opening makes no model calls.');
  // Keep the real renderer while bounding software-rendering cost during the 38 s opening.
  await driver.page.getByRole('button', { name: 'Map', exact: true }).click();
}

async function startJourney(driver: DriverHarness, index = 0) {
  await startOpening(driver);
  await driver.page.clock.runFor(1000);
  expect(driver.gateway.calls).toHaveLength(index);
  expect(driver.adversary.calls).toHaveLength(index);
  for (let elapsed = 1000; elapsed < 45_000 && driver.gateway.calls.length === index; elapsed += 1000) {
    await driver.page.clock.runFor(1000);
  }
  const request = await driver.gateway.waitForRequest(index);
  await driver.page.clock.runFor(32);
  const carbon = await driver.adversary.waitForRequest(index);
  expect(carbon.run_id).toBe(request.run_id);
  await expect(driver.page.locator('.journey-status')).toContainText('Carbon is choosing');
  await expect(driver.page.getByRole('button', { name: 'Export results', exact: true })).toBeDisabled();
  return request;
}

interface Report { schemaVersion: string; status: string; retainedWindows: RunTrace[]; snapshot: { decisions: number; seconds: number } }
async function exportReport(driver: DriverHarness): Promise<Report> {
  const pending = driver.page.waitForEvent('download');
  await driver.page.getByRole('button', { name: 'Export results', exact: true }).click();
  const download = await pending;
  expect(download.suggestedFilename()).toBe('gridlocked-nyc-journey.json');
  const path = await download.path();
  expect(path).not.toBeNull();
  const report = JSON.parse(await readFile(path!, 'utf8')) as Report;
  expect(report.schemaVersion).toBe('gridlocked-journey.v1');
  return report;
}

function liveWindow(report: Report) {
  expect(report.retainedWindows).toHaveLength(2);
  const [opening, live] = report.retainedWindows;
  expect(opening).toMatchObject({ modelSource: 'scripted-demo', modelPolicy: { service: 'scripted', modelId: null },
    terminal: { reason: 'scripted-complete' }, scriptedOpening: { complete: true }, steps: [] });
  expect(opening.adversary).toBeUndefined();
  expect(live.modelSource).toBe('live-model');
  expect(live.steps[0].observation.simulation_time).toBe(0);
  return live;
}

for (const exit of ['stop', 'hidden'] as const) {
  test(`${exit} during the labelled scripted opening prevents the transition to model calls`, async ({ driver }) => {
    await openJourney(driver);
    await startOpening(driver);
    await driver.page.clock.runFor(1500);
    expect(driver.gateway.calls).toHaveLength(0);
    expect(driver.adversary.calls).toHaveLength(0);
    await expect(driver.page.locator('.journey-panel')).toContainText('Scripted demonstration');
    if (exit === 'stop') await driver.page.getByRole('button', { name: 'Stop journey', exact: true }).click();
    else await driver.page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect(driver.page.locator('.journey-status')).toHaveText('Journey stopped');
    const stopped = await exportReport(driver);
    expect(stopped.retainedWindows).toHaveLength(1);
    expect(stopped.retainedWindows[0]).toMatchObject({ modelSource: 'scripted-demo',
      modelPolicy: { service: 'scripted', modelId: null }, terminal: { reason: 'interrupted' },
      scriptedOpening: { complete: false }, steps: [] });
    expect(stopped.retainedWindows[0].adversary).toBeUndefined();
    expect(stopped.snapshot.decisions).toBe(0);
    expect(stopped.snapshot.seconds).toBeGreaterThan(1);
    if (exit === 'hidden') await driver.page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: false });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await driver.page.clock.runFor(45_000);
    expect(driver.gateway.calls).toHaveLength(0);
    expect(driver.adversary.calls).toHaveLength(0);
    expect(driver.routeRequests).toHaveLength(1);
    expect(await exportReport(driver)).toEqual(stopped);
  });
}

test('journey start, stop and export preserve a frozen final window despite a late driver response', async ({ driver }) => {
  await openJourney(driver);
  await startJourney(driver);
  await driver.gateway.reply(0);
  await driver.page.clock.fastForward(4000);
  await expect(driver.page.locator('.journey-agents')).toContainText('0 decisions');
  await driver.adversary.reply(0, { decision: { action: 'attack', event_type: 'cut-in-vehicle', trigger_seconds: 0,
    parameters: { distanceAheadM: 80, approachSeconds: 1 }, durationSeconds: 20, reason: 'Offline cut-in fixture.' } });
  await driver.page.clock.runFor(1200);
  expect((await driver.gateway.waitForRequest(1)).observation.lead_gap_m).not.toBeNull();
  await expect(driver.page.locator('.journey-agents')).toContainText('1 decisions');
  await expect(driver.page.locator('.journey-panel')).toContainText('Car cutting in');
  await driver.page.getByRole('button', { name: 'Stop journey', exact: true }).click();
  await expect(driver.page.locator('.journey-status')).toHaveText('Journey stopped');
  const stopped = await exportReport(driver);
  const live = liveWindow(stopped);
  expect(live.terminal.reason).toBe('interrupted');
  expect(live.adversary?.decisions[0].decision.event_type).toBe('cut-in-vehicle');
  expect(stopped.snapshot.seconds).toBeGreaterThan(stopped.retainedWindows[0].frames.at(-1)!.time + 1);
  expect(stopped.snapshot.decisions).toBe(1);
  await driver.gateway.reply(1, { decision: { reason: 'STALE JOURNEY RESPONSE' } });
  await driver.page.clock.fastForward(5000);
  expect(await exportReport(driver)).toEqual(stopped);
  expect(driver.gateway.calls).toHaveLength(2);
  expect(driver.adversary.calls).toHaveLength(1);
  expect(driver.routeRequests).toHaveLength(1);
});

test('a route reset retains its interrupted journey export and a new start excludes abandoned callbacks', async ({ driver }) => {
  await openJourney(driver);
  const old = await startJourney(driver);
  await driver.page.getByRole('button', { name: 'Close journey panel', exact: true }).click();
  await driver.planRoute('long');
  await expect(driver.page.getByRole('slider', { name: 'Simulation timeline' })).toBeVisible();
  await driver.page.getByRole('button', { name: 'NYC journey', exact: true }).click();
  const reset = await exportReport(driver);
  expect(reset.status).toBe('idle');
  expect(liveWindow(reset).runId).toBe(old.run_id);
  expect(liveWindow(reset).terminal.reason).toBe('interrupted');
  const fresh = await startJourney(driver, 1);
  expect(fresh.run_id).not.toBe(old.run_id);
  await driver.gateway.reply(0, { decision: { reason: 'ABANDONED DRIVER' } });
  await driver.adversary.reply(0);
  await driver.adversary.reply(1);
  await driver.gateway.reply(1, { decision: { reason: 'CURRENT JOURNEY' } });
  await driver.page.clock.runFor(1200);
  await expect(driver.page.locator('.journey-agents')).toContainText('1 decisions');
  await driver.page.getByRole('button', { name: 'Stop journey', exact: true }).click();
  const restarted = await exportReport(driver);
  expect(liveWindow(restarted).runId).toBe(fresh.run_id);
  expect(restarted.retainedWindows.every(trace => !reset.retainedWindows.some(oldTrace => oldTrace.runId === trace.runId))).toBe(true);
  expect(JSON.stringify(restarted)).not.toContain('ABANDONED DRIVER');
  expect(liveWindow(restarted).steps[0].response?.decision.reason).toBe('CURRENT JOURNEY');
});

test('journey HTTP failures stop without retry and export bounded request provenance', async ({ driver }) => {
  await openJourney(driver);
  await startJourney(driver);
  await driver.adversary.reply(0);
  await driver.gateway.reply(0, { status: 502, raw: { detail: {
    code: 'invalid_output', request_id: '12345678-1234-4234-8234-123456789abc',
    message: 'The model returned invalid output.', raw: 'PRIVATE_PROVIDER_BODY',
  } } });
  await expect(driver.page.locator('.journey-panel [role=alert]')).toHaveText('The model returned invalid output.');
  await expect(driver.page.locator('.journey-status')).toHaveText('Journey interrupted');
  const report = await exportReport(driver);
  expect(report.status).toBe('error');
  expect(liveWindow(report).terminal.reason).toBe('error');
  expect(liveWindow(report).steps[0]).toMatchObject({ response: null, failure: {
    code: 'invalid_output', requestId: '12345678-1234-4234-8234-123456789abc',
  } });
  expect(JSON.stringify(report)).not.toContain('PRIVATE_PROVIDER_BODY');
  await driver.page.clock.fastForward(10_000);
  expect(driver.gateway.calls).toHaveLength(1);
  expect(driver.routeRequests).toHaveLength(1);
});

test('hiding the tab stops a pending journey and returning never revives late responses', async ({ driver }) => {
  await openJourney(driver);
  await startJourney(driver);
  await driver.page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(driver.page.locator('.journey-status')).toHaveText('Journey stopped');
  const stopped = await exportReport(driver);
  expect(liveWindow(stopped).steps[0].response).toBeNull();
  await driver.page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await driver.gateway.reply(0);
  await driver.adversary.reply(0);
  await driver.page.clock.fastForward(5000);
  await expect(driver.page.locator('.journey-status')).toHaveText('Journey stopped');
  expect(await exportReport(driver)).toEqual(stopped);
  expect(driver.gateway.calls).toHaveLength(1);
  expect(driver.routeRequests).toHaveLength(1);
});

test('camera evidence hands off an active journey to its existing matching street drive', async ({ driver }) => {
  driver.evidence.add(FIXTURE_CAMERAS.start, 'blocked', 3);
  await openJourney(driver);
  await driver.page.getByRole('button', { name: 'Close journey panel', exact: true }).click();
  await driver.page.getByRole('button', { name: 'Open workspace tools', exact: true }).click();
  await driver.page.locator('.rail-mode-switch').getByRole('button', { name: 'Cameras', exact: true }).click();
  await driver.page.getByRole('textbox', { name: 'Search cameras', exact: true }).fill(FIXTURE_CAMERAS.start.name);
  await driver.page.locator('.camera-row').filter({ hasText: FIXTURE_CAMERAS.start.name }).click();
  await driver.planRoute('long');
  await driver.page.getByRole('button', { name: 'Camera evidence', exact: true }).click();
  await driver.page.getByRole('button', { name: /^Load cached clip/ }).click();
  const token = driver.page.getByLabel('Operator token', { exact: true });
  if (!await token.isVisible()) await driver.page.locator('.evidence-operator > summary').click();
  await token.fill(EVIDENCE_TOKEN);
  await driver.page.getByRole('button', { name: 'Load cached observations · no model call', exact: true }).click();
  await expect(driver.page.locator('.evidence-result-summary')).toContainText('Extraction completed');
  await driver.page.getByRole('button', { name: 'Close evidence panel', exact: true }).click();
  await driver.page.getByRole('button', { name: 'NYC journey', exact: true }).click();
  const old = await startJourney(driver);
  await driver.page.getByRole('button', { name: 'Close journey panel', exact: true }).click();
  await driver.page.getByRole('button', { name: 'Camera evidence', exact: true }).click();
  await driver.page.getByRole('button', { name: 'Drive here with this evidence', exact: true }).click();
  await expect(driver.page.locator('.journey-launch')).toHaveText('NYC journey');
  await expect(driver.page.locator('.driver-panel')).toBeVisible();
  await expect(driver.page.getByLabel('Scenario preset')).toHaveValue('stalled-vehicle');
  await driver.gateway.reply(0, { decision: { reason: 'ABANDONED JOURNEY' } });
  await driver.adversary.reply(0);
  await driver.startDriver('event');
  const fresh = await driver.gateway.waitForRequest(1);
  expect(fresh.run_id).not.toBe(old.run_id);
  expect(fresh.observation.route_label).toContain('traffic count from camera evidence');
  expect(fresh.observation.lead_gap_m).toBeGreaterThan(0);
});
