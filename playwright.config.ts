import { defineConfig } from '@playwright/test';

const port = Number(process.env.PLAYWRIGHT_PORT ?? 5191);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('PLAYWRIGHT_PORT must be an integer from 1024 to 65535');
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './tests/browser',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  failOnFlakyTests: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : 2,
  maxFailures: process.env.CI ? 3 : 0,
  globalTimeout: process.env.CI ? 600_000 : undefined,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    browserName: 'chromium',
    serviceWorkers: 'block',
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'on-first-retry',
    launchOptions: {
      args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
    },
  },
  projects: [
    // Preserve the desktop layout while bounding software raster cost on CI.
    // These tests compare rendered transforms, not pixel appearance.
    { name: 'desktop-chromium', use: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: process.env.CI ? 0.5 : 1 } },
    { name: 'mobile-chromium', use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
  webServer: {
    command: `npm run preview -- --port ${port} --strictPort`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
