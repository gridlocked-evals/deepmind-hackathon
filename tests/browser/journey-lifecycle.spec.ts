import { readFile } from 'node:fs/promises';
import { test, expect, type DriverHarness } from './fixtures';
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

async function startJourney(driver: DriverHarness, index = 0) {
  await driver.page.getByRole('button', { name: /^(Start journey|Start new journey)$/ }).click();
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
  await expect(driver.page.locator('.journey-events')).toContainText('Car cutting in');
  await driver.page.getByRole('button', { name: 'Stop journey', exact: true }).click();
  await expect(driver.page.locator('.journey-status')).toHaveText('Journey stopped');
  const stopped = await exportReport(driver);
  expect(stopped.retainedWindows).toHaveLength(1);
  expect(stopped.retainedWindows[0].terminal.reason).toBe('interrupted');
  expect(stopped.retainedWindows[0].adversary?.decisions[0].decision.event_type).toBe('cut-in-vehicle');
  expect(stopped.snapshot.seconds).toBeGreaterThan(1);
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
  expect(reset.retainedWindows).toHaveLength(1);
  expect(reset.retainedWindows[0].runId).toBe(old.run_id);
  expect(reset.retainedWindows[0].terminal.reason).toBe('interrupted');
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
  expect(restarted.retainedWindows.map(trace => trace.runId)).toEqual([fresh.run_id]);
  expect(JSON.stringify(restarted)).not.toContain('ABANDONED DRIVER');
  expect(restarted.retainedWindows[0].steps[0].response?.decision.reason).toBe('CURRENT JOURNEY');
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
  expect(report.retainedWindows[0].terminal.reason).toBe('error');
  expect(report.retainedWindows[0].steps[0]).toMatchObject({ response: null, failure: {
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
  expect(stopped.retainedWindows[0].steps[0].response).toBeNull();
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
