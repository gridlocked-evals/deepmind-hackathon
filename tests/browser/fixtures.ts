import { test as base, expect, type BrowserContext, type Page, type Request, type Route } from '@playwright/test';
import type { DriverDecision, DriverObservation, DriverResponse } from '../../src/agent/types';
import type { ModelGatewayStatus } from '../../src/models/types';
import type { CameraSource } from '../../src/types';

export { expect };

export const FIXTURE_MODEL = 'gemini-browser-fixture';
export const FIXTURE_CAMERAS = {
  start: camera('start', 'Browser fixture start', [-74.011485, 40.722891]),
  short: camera('short', 'Browser fixture nearby destination', [-74.011485, 40.722999]),
  long: camera('long', 'Browser fixture distant destination', [-74.011485, 40.731884]),
};

function camera(id: string, name: string, coordinates: [number, number]): CameraSource {
  return {
    id: `browser-${id}`, name, coordinates, kind: 'snapshot', provider: 'Offline browser fixture',
    borough: 'Manhattan', sourceUrl: 'https://example.invalid/browser-camera',
    playbackUrl: `/api/browser-fixtures/cameras/${id}.svg`, availability: 'connected',
    capturedAt: null, fetchedAt: null,
  };
}

const MODEL_STATUS: ModelGatewayStatus = {
  schema_version: 1, key_profile: 'primary', config_hash: 'offline-browser-fixture', rates_expired: false,
  services: [{
    service: 'hydrogen', model_id: FIXTURE_MODEL, state: 'configured', reason: null,
    configured: true, enabled: true, optional: false, last_request_id: null,
    last_provider_request_id: null, last_latency_ms: null, verified_at: null,
    usage_reported: false, project_quota: 'Offline fixture; no provider calls',
  }],
  budgets: {
    charged_usd: 0, limit_usd: 0, demo_reserve_usd: 0, stages: {}, blocks: [],
    unresolved_world_requests: [], notice: 'Offline browser test fixture',
  },
};

const BASEMAP = {
  version: 8, name: 'Offline browser fixture',
  glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
  sources: {}, layers: [{ id: 'ground', type: 'background', paint: { 'background-color': '#09121b' } }],
};

export interface DecisionRequest {
  run_id: string;
  step: number;
  observation: DriverObservation;
}

export interface PendingDecision extends DecisionRequest {
  index: number;
  body: DecisionRequest;
  request: Request;
  replied: boolean;
}

export interface DecisionReply extends Partial<Omit<DriverResponse, 'decision'>> {
  decision?: Partial<DriverDecision>;
  status?: number;
  /** Supply an arbitrary payload to exercise malformed or error responses. */
  raw?: unknown;
  /** Literal JSON response bytes, including intentionally invalid JSON. */
  body?: string;
}

const DEFAULT_DECISION: DriverDecision = {
  action: 'accelerate', target_speed_mps: 8, confidence: 0.98,
  reason: 'Offline fixture: continue along the selected route.',
};

/** Hold real HTTP fetches until the test releases a particular response. */
export class Gateway {
  readonly calls: PendingDecision[] = [];
  private readonly routes = new Map<number, Route>();
  private automatic: Partial<DriverDecision> | undefined;

  async capture(route: Route) {
    const body = route.request().postDataJSON() as DecisionRequest;
    const index = this.calls.length;
    this.calls.push({ index, ...body, body, request: route.request(), replied: false });
    this.routes.set(index, route);
    if (this.automatic) await this.reply(index, { decision: this.automatic });
  }

  async waitForRequest(index: number): Promise<PendingDecision> {
    await expect.poll(() => this.calls.length, { message: `Waiting for driver request ${index}` }).toBeGreaterThan(index);
    return this.calls[index];
  }

  async reply(index: number, overrides: DecisionReply = {}) {
    const call = await this.waitForRequest(index);
    const route = this.routes.get(index);
    if (!route || call.replied) throw new Error(`Driver request ${index} was already replied to`);
    const { status = 200, raw, body, decision, ...envelope } = overrides;
    const response: DriverResponse = {
      run_id: call.run_id, step: call.step, max_steps: 12,
      model_id: FIXTURE_MODEL, request_id: `browser-decision-${index}`,
      decision: { ...DEFAULT_DECISION, ...decision }, ...envelope,
    };
    call.replied = true;
    this.routes.delete(index);
    await route.fulfill({ status, ...(body === undefined
      ? { json: Object.hasOwn(overrides, 'raw') ? raw : response }
      : { contentType: 'application/json', body }) });
  }

  /** Only future requests are answered automatically; held requests stay held. */
  autoReply(decision: Partial<DriverDecision> = {}) { this.automatic = decision; }

  manualReplies() { this.automatic = undefined; }

  async dispose() {
    await Promise.allSettled([...this.routes.values()].map(route => route.abort('aborted')));
    this.routes.clear();
  }
}

export class DriverHarness {
  readonly gateway = new Gateway();
  readonly unexpectedRequests: string[] = [];
  readonly routeRequests: string[] = [];
  readonly pageErrors: string[] = [];
  readonly consoleErrors: string[] = [];
  private readonly pendingBasemap: Route[] = [];
  private delayedBasemap = false;
  private opened = false;

  constructor(readonly page: Page, private readonly context: BrowserContext, private readonly baseURL: string) {
    page.on('pageerror', error => { this.pageErrors.push(error.stack ?? error.message); });
    page.on('console', message => {
      if (message.type() === 'error' && this.consoleErrors.length < 50) this.consoleErrors.push(message.text());
    });
  }

  async installRoutes() {
    const origin = new URL(this.baseURL).origin;
    await this.context.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const local = url.origin === origin;
      const get = request.method() === 'GET';
      if (local && get && url.pathname === '/api/models/status') {
        await route.fulfill({ json: MODEL_STATUS });
      } else if (local && get && url.pathname === '/api/cameras') {
        await route.fulfill({ json: { cameras: Object.values(FIXTURE_CAMERAS), metadata: { mode: 'connected' } } });
      } else if (local && get && url.pathname === '/api/vision/cameras') {
        await route.fulfill({ json: { cameras: [], metadata: { mode: 'connected' } } });
      } else if (local && url.pathname === '/api/driver/decision' && request.method() === 'POST') {
        await this.gateway.capture(route);
      } else if (get && url.origin === 'https://router.project-osrm.org' && url.pathname.startsWith('/route/v1/driving/')) {
        const destination = (['short', 'long'] as const).find(kind => url.pathname === routePath(kind));
        if (!destination || url.searchParams.get('overview') !== 'full' || url.searchParams.get('geometries') !== 'geojson') {
          await this.deny(route, 'Route did not use the selected fixture camera endpoints');
          return;
        }
        this.routeRequests.push(request.url());
        await route.fulfill({ headers: { 'access-control-allow-origin': '*' }, json: streetRoute(destination) });
      } else if (get && url.href === 'https://tiles.openfreemap.org/styles/liberty') {
        if (this.delayedBasemap) this.pendingBasemap.push(route);
        else await route.fulfill({ headers: { 'access-control-allow-origin': '*' }, json: BASEMAP });
      } else if (get && url.origin === 'https://tiles.openfreemap.org' && /^\/fonts\/[^/]+\/\d+-\d+\.pbf$/.test(url.pathname)) {
        // Empty protobuf = no glyphs. The authored Three scene still renders normally.
        await route.fulfill({ contentType: 'application/x-protobuf', headers: { 'access-control-allow-origin': '*' }, body: '' });
      } else if (get && url.origin === 'https://fonts.googleapis.com' && url.pathname === '/css2') {
        await route.fulfill({ contentType: 'text/css', body: '/* Browser tests use local fallback fonts. */' });
      } else if (get && ((local && /^\/api\/feeds\/511ny-\d+\/index\.m3u8$/.test(url.pathname))
        || (url.origin === 'https://s9.nysdot.skyvdn.com' && /^\/rtplive\/R11_\d+\/playlist\.m3u8$/.test(url.pathname)))) {
        await route.fulfill({ contentType: 'application/vnd.apple.mpegurl', headers: { 'access-control-allow-origin': '*' },
          body: '#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-ENDLIST\n' });
      } else if (local && get && /^\/api\/browser-fixtures\/cameras\/(start|short|long)\.svg$/.test(url.pathname)) {
        await route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="36"><rect width="64" height="36" fill="#09121b"/></svg>' });
      } else if (local && get && (url.pathname === '/' || url.pathname === '/index.html'
        || url.pathname === '/favicon.svg' || /^\/assets\/[^/]+\.(js|css|woff2?|svg|png|webp)$/.test(url.pathname))) {
        await route.continue();
      } else {
        await this.deny(route, 'Unmocked request');
      }
    });
    await this.context.routeWebSocket('**/*', socket => {
      this.unexpectedRequests.push(`Unexpected WebSocket ${socket.url()}`);
      socket.close({ code: 1008, reason: 'Network is disabled in browser lifecycle tests' });
    });
  }

  private async deny(route: Route, reason: string) {
    this.unexpectedRequests.push(`${reason}: ${route.request().method()} ${route.request().url()}`);
    await route.abort('blockedbyclient');
  }

  async open(options: { unabortableDecisions?: boolean; delayedBasemap?: boolean } = {}) {
    if (this.opened) throw new Error('Create a new test page instead of opening the fixture twice');
    this.opened = true;
    this.delayedBasemap = options.delayedBasemap ?? false;
    await this.page.clock.install({ time: new Date('2026-11-21T00:00:00Z') });
    if (options.unabortableDecisions) await this.useUnabortableDecisions();
    await this.page.goto(this.baseURL);
    await expect(this.page.locator('.maplibregl-canvas')).toBeVisible();
    await expect(this.page.locator('.city-map-loading, .map-loading')).toHaveCount(0);
    await expect(this.page.locator('.map-error')).toHaveCount(0);
    if (options.delayedBasemap) {
      await expect.poll(() => this.pendingBasemap.length, { message: 'Basemap request held while the fallback scene loads' }).toBeGreaterThan(0);
      // Preserve the pending fetch's real 10-second timeout. Jumping to the
      // usual one-minute boundary would cancel it before the test releases it.
      await this.page.clock.pauseAt(await this.page.evaluate(() => Date.now() + 100));
    }
    // Close the mobile modal before interacting with the rest of the workspace.
    await this.closeTools();
    const cameras = this.page.locator('.topbar-actions').getByRole('button', { name: 'Cameras', exact: true });
    if (await cameras.getAttribute('aria-pressed') === 'true') await cameras.click();
    const pause = this.page.getByRole('button', { name: 'Pause simulation', exact: true });
    if (await pause.isVisible()) await pause.click();
    // Mobile hides the reset button; Home on the real range input seeks to zero.
    await this.page.getByRole('slider', { name: 'Simulation timeline' }).press('Home');
    // Boot with ordinary timers, then advance to a fixed pause boundary. All
    // subsequent animation and timeout progression belongs to the test.
    if (!options.delayedBasemap) await this.page.clock.pauseAt(new Date('2026-11-21T00:01:00Z'));
  }

  async releaseBasemap() {
    await expect.poll(() => this.pendingBasemap.length, { message: 'Waiting for the delayed basemap request' }).toBeGreaterThan(0);
    this.delayedBasemap = false;
    await Promise.all(this.pendingBasemap.splice(0).map(route => route.fulfill({
      headers: { 'access-control-allow-origin': '*' }, json: BASEMAP,
    })));
  }

  async dispose() {
    await this.gateway.dispose();
    await Promise.allSettled(this.pendingBasemap.splice(0).map(route => route.abort('aborted')));
  }

  /** Keep native fetch + routing, but model a transport that ignores abort. */
  async useUnabortableDecisions() {
    await this.page.addInitScript(() => {
      const nativeFetch = window.fetch.bind(window);
      const observed: boolean[] = [];
      Object.defineProperty(window, '__driverDecisionSignals', { value: observed });
      window.fetch = (input, init) => {
        const url = new URL(input instanceof Request ? input.url : String(input), location.href);
        if (url.origin === location.origin && url.pathname === '/api/driver/decision') {
          observed.push(!!(init?.signal ?? (input instanceof Request ? input.signal : undefined)));
          // The production call uses URL + init. Preserve all options except cancellation.
          return nativeFetch(input, { ...init, signal: null });
        }
        return nativeFetch(input, init);
      };
    });
  }

  async decisionSignals(): Promise<boolean[]> {
    return this.page.evaluate(() => (window as Window & { __driverDecisionSignals?: boolean[] }).__driverDecisionSignals ?? []);
  }

  async closeTools() {
    const close = this.page.getByRole('button', { name: 'Collapse workspace tools', exact: true });
    if (await close.isVisible()) await close.click();
  }

  async openDriver() {
    if (await this.page.locator('.driver-panel').isVisible()) return;
    if (await this.page.locator('.source-rail').isVisible()) {
      await this.page.locator('.rail-mode-switch').getByRole('button', { name: 'Driver', exact: true }).click();
    } else {
      await this.page.getByRole('button', { name: 'Gemini driver', exact: true }).click();
    }
    await expect(this.page.locator('.driver-panel')).toBeVisible();
  }

  /** Production closes the tools on mobile; call openDriver to inspect the panel again. */
  async startDriver() {
    await this.openDriver();
    await this.page.getByRole('button', { name: /^(Start|Restart) Gemini drive$/ }).click();
  }

  async planRoute(kind: 'short' | 'long') {
    if (!await this.page.locator('.source-rail').isVisible()) {
      await this.page.getByRole('button', { name: 'Open workspace tools', exact: true }).click();
    }
    await this.page.locator('.rail-mode-switch').getByRole('button', { name: 'Route', exact: true }).click();
    const clear = this.page.getByRole('button', { name: 'Clear route', exact: true });
    if (await clear.isVisible()) await clear.click();
    for (const [label, point] of [['start', FIXTURE_CAMERAS.start], ['destination', FIXTURE_CAMERAS[kind]]] as const) {
      await this.page.getByRole('textbox', { name: `Search ${label} cameras`, exact: true }).fill(point.name);
      await this.page.locator('.endpoint-results').getByRole('button', { name: point.name }).click();
    }
    await this.page.getByRole('button', { name: 'Find driving route', exact: true }).click();
    await this.page.getByRole('button', { name: 'Drive this route', exact: true }).click();
    await this.closeTools();
    const pause = this.page.getByRole('button', { name: 'Pause simulation', exact: true });
    if (await pause.isVisible()) await pause.click();
  }

  async time() { return Number(await this.page.getByRole('slider', { name: 'Simulation timeline' }).inputValue()); }

  async decisionCount() {
    // The transport's text is visually collapsed at narrow widths.
    const text = await this.page.locator('.playback-identity').textContent();
    return Number(text?.match(/(\d+)\/12 decisions/)?.[1] ?? 0);
  }
}

function routePath(kind: 'short' | 'long') {
  const points = [FIXTURE_CAMERAS.start, FIXTURE_CAMERAS[kind]]
    .map(point => point.coordinates.map(value => value.toFixed(6)).join(',')).join(';');
  return `/route/v1/driving/${points}`;
}

function streetRoute(kind: 'short' | 'long') {
  const distance = kind === 'short' ? 12 : 1000;
  return {
    code: 'Ok', waypoints: [{ distance: 0 }, { distance: 0 }],
    routes: [{
      distance, duration: distance / 8,
      geometry: { type: 'LineString', coordinates: [FIXTURE_CAMERAS.start.coordinates, FIXTURE_CAMERAS[kind].coordinates] },
      legs: [{ steps: [
        { name: 'Fixture Street', distance, maneuver: { type: 'depart' } },
        { name: '', distance: 0, maneuver: { type: 'arrive' } },
      ] }],
    }],
  };
}

export const test = base.extend<{ driver: DriverHarness }>({
  driver: async ({ page, context, baseURL }, use, testInfo) => {
    if (!baseURL) throw new Error('Browser lifecycle tests require the isolated preview baseURL');
    const driver = new DriverHarness(page, context, baseURL);
    await driver.installRoutes();
    try {
      await use(driver);
    } finally {
      await driver.dispose();
      if (testInfo.status !== testInfo.expectedStatus || driver.unexpectedRequests.length || driver.pageErrors.length) {
        await testInfo.attach('browser-diagnostics.json', {
          contentType: 'application/json',
          body: JSON.stringify({
            gatewayCalls: driver.gateway.calls.map(({ index, body, replied }) => ({ index, body, replied })),
            routeRequests: driver.routeRequests,
            unexpectedRequests: driver.unexpectedRequests,
            pageErrors: driver.pageErrors,
            consoleErrors: driver.consoleErrors,
          }, null, 2),
        });
      }
      expect(driver.unexpectedRequests, 'All APIs and external resources must be mocked; no backend or paid providers may be called').toEqual([]);
      expect(driver.pageErrors, 'The real application must not raise uncaught browser errors').toEqual([]);
    }
  },
});
