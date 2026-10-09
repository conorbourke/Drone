/**
 * Phase 2 acceptance (docs/phases/PHASE2.md section 7): uploaded images produce a starting
 * model, edits update the model and estimates instantly, and two versions can be compared.
 *
 * Upload two generated PNGs, run a reading (the backend answers with
 * backend/tests/fixtures/vision_fake_response.json, see playwright.config.ts), accept the
 * proposal, see the model and the estimates change, drag the span handle in the top drawing
 * and see the span number and the estimates change, save two versions and open the
 * comparison view with both outlines and the table.
 */
import { expect, test, type Locator, type Page } from '@playwright/test';
import zlib from 'node:zlib';
import { TEST_PASSWORD } from '../playwright.config';

const byId = (page: Page, id: string): Locator => page.getByTestId(id);

// ---------- A tiny PNG encoder, so the test needs no image files or libraries ----------

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(buf: Buffer): number {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

/** An RGB PNG of a simple aircraft-like silhouette (a cross) on a sky-blue background. */
function makePng(width: number, height: number, hue: [number, number, number]): Buffer {
  const rows: Buffer[] = [];
  for (let y = 0; y < height; y++) {
    const row = Buffer.alloc(1 + width * 3);
    for (let x = 0; x < width; x++) {
      const wing = Math.abs(y - height / 2) < height * 0.06;
      const body = Math.abs(x - width / 2) < width * 0.05;
      const [r, g, b] = wing || body ? hue : [200, 225, 245];
      row[1 + x * 3] = r;
      row[2 + x * 3] = g;
      row[3 + x * 3] = b;
    }
    rows.push(row);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // colour type RGB
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(Buffer.concat(rows))),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

// ---------- helpers ----------

async function expectSaved(page: Page): Promise<void> {
  await expect(byId(page, 'draft-status')).toHaveText('Saved');
}

async function numberAttr(locator: Locator, attr = 'data-value'): Promise<number> {
  return Number(await locator.getAttribute(attr));
}

async function saveVersion(page: Page, name: string, number: number): Promise<void> {
  await byId(page, 'versions-save').click();
  await byId(page, 'version-name').fill(name);
  await byId(page, 'version-save-confirm').click();
  await expect(page.locator(`[data-testid="version-item"][data-version-number="${number}"]`)).toBeVisible();
}

test.describe('Phase 2 acceptance', () => {
  test('images to a starting model, live edits, and a version comparison', async ({ page }) => {
    let projectId = 0;

    await test.step('sign in and create a project', async () => {
      await page.goto('/login');
      await byId(page, 'login-password').fill(TEST_PASSWORD);
      await byId(page, 'login-submit').click();
      await expect(page).not.toHaveURL(/\/login/);
      await byId(page, 'projects-new').click();
      await byId(page, 'project-name').fill(`Phase 2 ${Date.now()}`);
      await byId(page, 'project-create').click();
      await expect(page).toHaveURL(/\/projects\/\d+\?tab=inputs$/);
      projectId = Number(/\/projects\/(\d+)/.exec(page.url())?.[1]);
      expect(projectId).toBeGreaterThan(0);
    });

    let massBefore = 0;
    let wingLoadingBefore = 0;
    let topOutlineBefore = '';
    await test.step('the Design tab shows the 3D view, three drawings and the estimates', async () => {
      await byId(page, 'tab-design').click();
      await expect(byId(page, 'view-3d')).toBeVisible();
      await expect(byId(page, 'view-3d').locator('canvas')).toHaveCount(1);
      for (const view of ['top', 'side', 'front']) await expect(byId(page, `drawing-${view}`)).toBeVisible();
      await expect(byId(page, 'estimates-panel')).toBeVisible();
      await expect(byId(page, 'field-wing.span_mm')).toHaveValue('1800');
      await expect(byId(page, 'slider-wing.span_mm')).toHaveValue('1800');
      massBefore = await numberAttr(byId(page, 'estimate-mass'));
      wingLoadingBefore = await numberAttr(byId(page, 'estimate-wing_loading'));
      expect(massBefore).toBeGreaterThan(1);
      await expect(byId(page, 'status-check.mtow')).toBeVisible();
      await expect(byId(page, 'a3-note')).toContainText('IAA authorisation');
      topOutlineBefore = (await byId(page, 'drawing-top').locator('polygon.shape-wing').first().getAttribute('points')) ?? '';
      expect(topOutlineBefore.length).toBeGreaterThan(10);
    });

    await test.step('upload two generated PNG images and set their views', async () => {
      await byId(page, 'tab-inputs').click();
      await byId(page, 'image-upload').setInputFiles([
        { name: 'render-top.png', mimeType: 'image/png', buffer: makePng(320, 200, [40, 60, 90]) },
        { name: 'render-side.png', mimeType: 'image/png', buffer: makePng(320, 160, [90, 60, 40]) },
      ]);
      await expect(byId(page, 'image-item')).toHaveCount(2);
      await expect(byId(page, 'image-view').nth(0)).toHaveValue('top');
      await expect(byId(page, 'image-view').nth(1)).toHaveValue('side');
      // Thumbnails load from the stored file.
      const loaded = await byId(page, 'image-item')
        .first()
        .locator('img')
        .evaluate((img: HTMLImageElement) => img.decode().then(() => img.naturalWidth));
      expect(loaded).toBe(320);
      // Change a view and see it persist.
      await byId(page, 'image-view').nth(1).selectOption('side');
      const listed = await page.request.get(`/api/projects/${projectId}/images`);
      expect((await listed.json()).map((i: { view: string }) => i.view)).toEqual(['top', 'side']);
    });

    await test.step('run a reading with the fake Claude response and review the proposal', async () => {
      await expect(byId(page, 'reading-unavailable')).toHaveCount(0);
      await expect(byId(page, 'reading-reference-parameter')).toHaveValue('wing.span_mm');
      await byId(page, 'reading-reference-value').fill('2000');
      await byId(page, 'reading-run').click();
      await expect(byId(page, 'reading-status')).toHaveText('Proposal ready', { timeout: 20_000 });
      await expect(byId(page, 'proposal-layout')).toContainText('Front tilt');
      await expect(page.locator('[data-testid="proposal-row"][data-path="wing.span_mm"]')).toContainText('2,000 mm');
      await expect(page.locator('[data-testid="proposal-row"][data-path="fuselage.length_mm"]')).toContainText('960 mm');
      await expect(page.locator('[data-testid="proposal-row"][data-path="tail.type"]')).toBeVisible();
      expect(await byId(page, 'proposal-row').count()).toBeGreaterThan(15);
    });

    await test.step('accept the proposal: select all and apply', async () => {
      await byId(page, 'proposal-select-none').click();
      await expect(byId(page, 'proposal-apply')).toBeDisabled();
      await byId(page, 'proposal-select-all').click();
      const boxes = byId(page, 'proposal-accept');
      const n = await boxes.count();
      for (let i = 0; i < n; i++) await expect(boxes.nth(i)).toBeChecked();
      await byId(page, 'proposal-apply').click();
      await expect(byId(page, 'proposal-applied')).toBeVisible();
      await expectSaved(page);
    });

    await test.step('the layout comparison is shown and offers each layout', async () => {
      await expect(byId(page, 'layout-compare')).toBeVisible();
      await expect(byId(page, 'layout-use-front_tilt')).toBeDisabled();
      await expect(byId(page, 'layout-use-quad_pusher')).toBeEnabled();
      await expect(byId(page, 'layout-compare')).toContainText('Q_TILT_MASK');
    });

    await test.step('the model and the estimates changed', async () => {
      await byId(page, 'tab-design').click();
      await expect(byId(page, 'field-wing.span_mm')).toHaveValue('2000');
      await expect(byId(page, 'field-fuselage.length_mm')).toHaveValue('960');
      await expect(byId(page, 'field-tail.type')).toHaveValue('inverted_v');
      const outline = await byId(page, 'drawing-top').locator('polygon.shape-wing').first().getAttribute('points');
      expect(outline).not.toBe(topOutlineBefore);
      expect(await numberAttr(byId(page, 'estimate-mass'))).not.toBeCloseTo(massBefore, 4);
      expect(await numberAttr(byId(page, 'estimate-wing_loading'))).not.toBeCloseTo(wingLoadingBefore, 4);
    });

    await test.step('save version v1', async () => {
      await saveVersion(page, 'v1', 1);
    });

    await test.step('drag the span handle in the top drawing: span and estimates update live', async () => {
      const wingLoading = await numberAttr(byId(page, 'estimate-wing_loading'));
      const handle = byId(page, 'drawing-top').getByTestId('handle-span');
      await expect(handle).toHaveAttribute('data-handle', 'span');
      await handle.scrollIntoViewIfNeeded();
      const box = await handle.locator('.handle-dot').boundingBox();
      expect(box).not.toBeNull();
      const x = box!.x + box!.width / 2;
      const y = box!.y + box!.height / 2;
      await page.mouse.move(x, y);
      await page.mouse.down();
      // Nose up: the starboard tip is on the right, so moving right lengthens the wing.
      await page.mouse.move(x + 25, y, { steps: 5 });
      await expect(byId(page, 'drawing-top').getByTestId('drag-label')).toContainText('Wingspan');
      await page.mouse.move(x + 50, y, { steps: 5 });
      await page.mouse.up();
      await expect(byId(page, 'drag-label')).toHaveCount(0);
      const span = Number(await byId(page, 'field-wing.span_mm').inputValue());
      expect(span).toBeGreaterThan(2050);
      expect(Number.isInteger(span)).toBe(true); // snapped to 1 mm
      await expect(byId(page, 'slider-wing.span_mm')).toHaveValue(String(span));
      expect(await numberAttr(byId(page, 'estimate-wing_loading'))).toBeLessThan(wingLoading);
      await expect(byId(page, 'draft-basis')).toHaveText('Draft based on v1 (modified)');
      await expectSaved(page);
    });

    await test.step('save version v2', async () => {
      await saveVersion(page, 'v2', 2);
    });

    await test.step('select both versions and open the comparison', async () => {
      await expect(byId(page, 'versions-compare')).toBeDisabled();
      await page.locator('[data-testid="versions-compare-select"][data-version-number="1"]').check();
      await page.locator('[data-testid="versions-compare-select"][data-version-number="2"]').check();
      await byId(page, 'versions-compare').click();
      await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/compare\\?versions=1,2$`));
      await expect(byId(page, 'compare-view')).toBeVisible();
      for (const view of ['top', 'side']) {
        const layers = byId(page, `compare-${view}`).locator('.overlay-layer');
        await expect(layers).toHaveCount(2);
        await expect(layers.nth(0)).toHaveAttribute('data-version-number', '1');
        await expect(layers.nth(1)).toHaveAttribute('data-version-number', '2');
      }
      await expect(byId(page, 'compare-legend')).toContainText('v1');
      await expect(byId(page, 'compare-legend')).toContainText('v2');
      const table = byId(page, 'compare-table');
      await expect(table).toBeVisible();
      await expect(table.locator('[data-row="span"] td.is-diff')).toHaveCount(1);
      await expect(table.locator('[data-row="wing_loading"] td.is-diff')).toHaveCount(1);
      await expect(table.locator('[data-row="layout"] td.is-diff')).toHaveCount(0);
      await byId(page, 'compare-back').click();
      await expect(page).toHaveURL(new RegExp(`/projects/${projectId}\\?tab=design$`));
    });
  });

  test('a reading starts in the background and is polled until done', async ({ page }) => {
    await page.goto('/login');
    await byId(page, 'login-password').fill(TEST_PASSWORD);
    await byId(page, 'login-submit').click();
    await expect(page).not.toHaveURL(/\/login/);
    const headers = { 'X-Requested-With': 'fetch' };
    const project = await (await page.request.post('/api/projects', { data: { name: `Poll ${Date.now()}` }, headers })).json();
    const upload = await page.request.post(`/api/projects/${project.id}/images`, {
      headers,
      multipart: { view: 'top', file: { name: 'a.png', mimeType: 'image/png', buffer: makePng(64, 48, [0, 0, 0]) } },
    });
    expect(upload.status()).toBe(201);
    const started = await page.request.post(`/api/projects/${project.id}/image-readings`, {
      headers,
      data: { reference: { parameter: 'wing.span_mm', value_mm: 1500 } },
    });
    expect(started.status()).toBe(202);
    const reading = await started.json();
    expect(reading.status).toBe('running');
    await expect
      .poll(async () => (await (await page.request.get(`/api/image-readings/${reading.id}`)).json()).status)
      .toBe('ok');
    // Reopening the Inputs tab shows the latest proposal again.
    await page.goto(`/projects/${project.id}?tab=inputs`);
    await expect(byId(page, 'reading-status')).toHaveText('Proposal ready');
    await expect(page.locator('[data-testid="proposal-row"][data-path="wing.span_mm"]')).toContainText('1,500 mm');
  });
});
