/**
 * Playwright configuration for the end-to-end suite.
 *
 * The web server is the real backend started from backend/ (migration + uvicorn through its
 * entrypoint) with the production static-serving path: it serves frontend/dist itself, so the
 * tests exercise exactly what the container runs. Build the frontend first (`npm run build`
 * in frontend/).
 *
 * Environment knobs:
 *   E2E_PORT                 port for the backend (default 4173)
 *   E2E_DATA_DIR             data directory, wiped on every server start (default
 *                            <os tmpdir>/vtol-e2e-data; the name must start with "vtol-e2e")
 *   PLAYWRIGHT_BROWSERS_PATH where Playwright keeps its browsers (honoured as usual); when a
 *                            preinstalled Chromium build 1194 exists there or under
 *                            /opt/pw-browsers, it is used directly through executablePath
 *   CI                       when set: never reuse a running server, one retry, no `.only`
 */
import { defineConfig, devices } from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '..');

const PORT = Number(process.env.E2E_PORT ?? 4173);
const BASE_URL = `http://127.0.0.1:${PORT}`;
const DATA_DIR = process.env.E2E_DATA_DIR ?? path.join(os.tmpdir(), 'vtol-e2e-data');
const STATIC_DIR = path.join(repoRoot, 'frontend', 'dist');

/** Owner password the backend is started with; the specs sign in with it. */
export const TEST_PASSWORD = 'test-password';

/**
 * Chromium build 1194 is the one @playwright/test 1.56 expects. When it is preinstalled (for
 * example at /opt/pw-browsers/chromium-1194 in the development container) use that binary
 * instead of downloading one. Otherwise Playwright resolves the browser itself, honouring
 * PLAYWRIGHT_BROWSERS_PATH.
 */
function preinstalledChromium(): string | undefined {
  const roots = [process.env.PLAYWRIGHT_BROWSERS_PATH, '/opt/pw-browsers'].filter(
    (root): root is string => typeof root === 'string' && root.length > 0,
  );
  for (const root of roots) {
    const candidates = [
      path.join(root, 'chromium-1194', 'chrome-linux', 'chrome'),
      path.join(root, 'chromium', 'chrome-linux', 'chrome'),
      path.join(root, 'chromium'), // may be a symlink straight to the binary
    ];
    for (const candidate of candidates) {
      try {
        const stat = fs.statSync(candidate); // follows symlinks
        if (stat.isFile()) return candidate;
      } catch {
        // not there; try the next candidate
      }
    }
  }
  return undefined;
}

const executablePath = preinstalledChromium();

export default defineConfig({
  testDir: path.join(here, 'tests'),
  outputDir: path.join(here, 'test-results'),
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never', outputFolder: path.join(here, 'playwright-report') }]]
    : [['list']],
  use: {
    baseURL: BASE_URL,
    headless: true,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'off',
    locale: 'en-IE',
    timezoneId: 'Europe/Dublin',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: executablePath ? { executablePath } : {},
      },
    },
  ],
  webServer: {
    command: 'sh ../e2e/scripts/start-backend.sh',
    cwd: path.join(repoRoot, 'backend'),
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: 'ignore',
    stderr: 'pipe',
    // Playwright merges these over process.env, so PATH and PLAYWRIGHT_* variables carry through.
    env: {
      APP_PASSWORD: TEST_PASSWORD,
      APP_SECRET_KEY: 'e2e-secret',
      APP_ENV: 'development',
      APP_STATIC_DIR: STATIC_DIR,
      APP_DATA_DIR: DATA_DIR,
      BACKUP_ENABLED: 'false',
      PORT: String(PORT),
    },
  },
});
