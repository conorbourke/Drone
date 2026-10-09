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
 *
 * The backend gets CLAUDE_FAKE_RESPONSE_FILE (absolute path of
 * backend/tests/fixtures/vision_fake_response.json), so "Read images with Claude" answers with
 * that canned reading and never calls the API, and CLAUDE_FAKE_CHAT_FILE (absolute path of
 * backend/tests/fixtures/chat_fake_script.json), so the assistant replays a scripted
 * conversation (one run_quick_analysis tool call, an answer quoting it, a proposal).
 * CLAUDE_FAKE_SUPPLIER_FILE (absolute path of e2e/fixtures/supplier_fake.json) makes "Check prices
 * now" on the Parts tab answer with a canned empty result (no listings, so no shop link is ever
 * contacted) instead of calling Claude with web search.
 * VALIDATION_ON_STARTUP=false keeps the validation suite from running at startup; the
 * Validation page then shows the committed snapshot (docs/validation/latest.json).
 * EXPORT_FAKE_GENERATOR=tests.export_fakes:generate_files makes "Generate files" on the Files tab
 * write a small, complete fake file set at once instead of running the CAD kernel (never in production).
 * MOULD_FAKE_GENERATOR=tests.export_fakes:generate_moulds does the same for "Generate moulds" on the
 * Full scale tab (Phase 7): a small fake mould set instead of minutes of mould CAD.
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
/** Canned Claude answer: the backend returns it instead of calling the API (never in production). */
const CLAUDE_FAKE_RESPONSE_FILE = path.join(repoRoot, 'backend', 'tests', 'fixtures', 'vision_fake_response.json');
/** Scripted assistant conversation (never in production). */
const CLAUDE_FAKE_CHAT_FILE = path.join(repoRoot, 'backend', 'tests', 'fixtures', 'chat_fake_script.json');
/** Canned supplier lookup (no listings found) for "Check prices now" (never in production). */
const CLAUDE_FAKE_SUPPLIER_FILE = path.join(here, 'fixtures', 'supplier_fake.json');

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
      CLAUDE_FAKE_RESPONSE_FILE,
      CLAUDE_FAKE_CHAT_FILE,
      CLAUDE_FAKE_SUPPLIER_FILE,
      VALIDATION_ON_STARTUP: 'false',
      // Phase 5: file exports use the fast fake generator (backend/tests/export_fakes.py) instead of
      // the CAD kernel, so "Generate files" finishes in about a second.
      EXPORT_FAKE_GENERATOR: 'tests.export_fakes:generate_files',
      // Phase 7: mould sets use the fast fake mould generator from the same module.
      MOULD_FAKE_GENERATOR: 'tests.export_fakes:generate_moulds',
    },
  },
});
