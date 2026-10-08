/**
 * Security checks against the production static-serving path:
 *  - backup downloads (and every other /api route) need a session;
 *  - the SPA fallback never serves a file outside frontend/dist;
 *  - the CSRF header and the security headers from the contract are enforced.
 */
import { expect, test, type APIRequestContext } from '@playwright/test';
import { TEST_PASSWORD } from '../playwright.config';

const FETCH_HEADERS = { 'X-Requested-With': 'fetch' };

async function signIn(request: APIRequestContext): Promise<void> {
  const response = await request.post('/api/auth/login', {
    data: { password: TEST_PASSWORD },
    headers: FETCH_HEADERS,
  });
  expect(response.status()).toBe(200);
}

test.describe('API access without a session', () => {
  test('a backup download needs a session', async ({ request }) => {
    const response = await request.get('/api/system/backups/app-20260101-000000.db');
    expect(response.status()).toBe(401);
    expect(response.headers()['content-type']).toContain('application/json');
    const body = (await response.json()) as { detail: unknown };
    expect(typeof body.detail).toBe('string');
    expect(response.headers()['cache-control']).toBe('no-store');
  });

  test('every other /api route needs a session; only health is public', async ({ request }) => {
    for (const path of ['/api/auth/me', '/api/projects', '/api/settings', '/api/system/info', '/api/system/backups', '/api/schema/design', '/api/parts/categories', '/api/nope']) {
      const response = await request.get(path);
      expect(response.status(), path).toBe(401);
      expect(response.headers()['content-type'], path).toContain('application/json');
    }
    const health = await request.get('/api/health');
    expect(health.status()).toBe(200);
    expect(await health.json()).toMatchObject({ status: 'ok', db: 'ok' });
  });

  test('state-changing requests without X-Requested-With: fetch are refused', async ({ request }) => {
    const login = await request.post('/api/auth/login', { data: { password: TEST_PASSWORD } });
    expect(login.status()).toBe(403);
    await signIn(request);
    const create = await request.post('/api/projects', { data: { name: 'csrf' } });
    expect(create.status()).toBe(403);
  });
});

test.describe('Backups with a session', () => {
  test('a backup can be created and downloaded; odd names never resolve outside the backups directory', async ({ request }) => {
    await signIn(request);
    const created = await request.post('/api/system/backups', { headers: FETCH_HEADERS });
    expect(created.status()).toBe(201);
    const entry = (await created.json()) as { name: string; size_bytes: number };
    expect(entry.name).toMatch(/^app-\d{8}-\d{6}\.db$/);

    const download = await request.get(`/api/system/backups/${entry.name}`);
    expect(download.status()).toBe(200);
    expect(download.headers()['content-type']).toBe('application/octet-stream');
    expect(download.headers()['content-disposition']).toContain('attachment');
    expect(download.headers()['cache-control']).toBe('no-store');
    const bytes = await download.body();
    expect(bytes.length).toBe(entry.size_bytes);
    expect(bytes.subarray(0, 15).toString('latin1')).toBe('SQLite format 3');

    for (const name of ['app.db', '..%2Fapp.db', '%2e%2e%2F%2e%2e%2Fapp.db', 'app-20260101-000000.db.bak', 'app-99999999-999999.db']) {
      const response = await request.get(`/api/system/backups/${name}`);
      expect(response.status(), name).toBe(404);
    }

    const unknownRoute = await request.get('/api/nope');
    expect(unknownRoute.status()).toBe(404);
    expect(unknownRoute.headers()['content-type']).toContain('application/json');
  });
});

test.describe('Static serving', () => {
  test('the SPA index and its hashed assets are served', async ({ request }) => {
    const index = await request.get('/');
    expect(index.status()).toBe(200);
    expect(index.headers()['content-type']).toContain('text/html');
    const html = await index.text();
    expect(html).toContain('<div id="root">');
    const script = /src="(\/assets\/[^"]+\.js)"/.exec(html)?.[1];
    const stylesheet = /href="(\/assets\/[^"]+\.css)"/.exec(html)?.[1];
    expect(script).toBeTruthy();
    expect(stylesheet).toBeTruthy();
    const js = await request.get(script!);
    expect(js.status()).toBe(200);
    expect(js.headers()['content-type']).toContain('javascript');
    const css = await request.get(stylesheet!);
    expect(css.status()).toBe(200);
    expect(css.headers()['content-type']).toContain('text/css');
    for (const route of ['/projects/123', '/projects/123?tab=design', '/settings', '/login', '/no/such/page']) {
      const response = await request.get(route);
      expect(response.status(), route).toBe(200);
      expect(response.headers()['content-type'], route).toContain('text/html');
      expect(await response.text(), route).toBe(html);
    }
  });

  test('the SPA fallback never serves a file outside dist', async ({ request }) => {
    const index = await request.get('/');
    const html = await index.text();
    const probes = [
      '/%2e%2e/backend/pyproject.toml',
      '/%2e%2e/%2e%2e/etc/passwd',
      '/backend/app/main.py',
      '/backend/pyproject.toml',
      '/frontend/package.json',
      '/.env',
      '/etc/passwd',
      '/index.html/%2e%2e/%2e%2e/backend/pyproject.toml',
      '/assets/%2e%2e/index.html',
      '/assets/%2e%2e/%2e%2e/backend/pyproject.toml',
      '/assets/%2e%2e/%2e%2e/%2e%2e/etc/passwd',
      '/favicon.svg/%2e%2e/%2e%2e/backend/pyproject.toml',
    ];
    for (const probe of probes) {
      const response = await request.get(probe);
      const body = await response.text();
      // Either the SPA index (any non-/api path) or a 404 from the assets mount; never file contents.
      expect([200, 404], probe).toContain(response.status());
      if (response.status() === 200) {
        expect(response.headers()['content-type'], probe).toContain('text/html');
        expect(body, probe).toBe(html);
      }
      expect(body, probe).not.toContain('[project]');
      expect(body, probe).not.toContain('root:x:');
      expect(body, probe).not.toContain('create_app');
    }
  });

  test('security headers are present', async ({ request }) => {
    const api = await request.get('/api/health');
    expect(api.headers()['x-content-type-options']).toBe('nosniff');
    expect(api.headers()['referrer-policy']).toBe('same-origin');
    expect(api.headers()['x-frame-options']).toBe('DENY');
    expect(api.headers()['cache-control']).toBe('no-store');
    const page = await request.get('/');
    expect(page.headers()['x-content-type-options']).toBe('nosniff');
    expect(page.headers()['x-frame-options']).toBe('DENY');
  });
});
