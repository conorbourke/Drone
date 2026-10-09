/**
 * Phase 5 acceptance (docs/phases/PHASE5.md section 5): generate the files for the default
 * design on the Files tab, see every piece ticked as fitting the printer envelope, the files
 * grouped by kind with what opens them, download the ZIP and the BOM, open a piece preview on
 * the bed outline and delete the export.
 *
 * The backend runs with EXPORT_FAKE_GENERATOR (playwright.config.ts), so the export writes the
 * small fake file set of backend/tests/export_fakes.py at once instead of running the CAD kernel.
 */
import fs from 'node:fs';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { TEST_PASSWORD } from '../playwright.config';

const byId = (page: Page, id: string): Locator => page.getByTestId(id);

test.describe('Phase 5 acceptance', () => {
  test('generate files, grouped downloads, ZIP, piece preview, delete', async ({ page }) => {
    test.setTimeout(120_000);

    await test.step('sign in, create a project with the default design and open Files', async () => {
      await page.goto('/login');
      await byId(page, 'login-password').fill(TEST_PASSWORD);
      await byId(page, 'login-submit').click();
      await expect(page).not.toHaveURL(/\/login/);
      await byId(page, 'projects-new').click();
      await byId(page, 'project-name').fill(`Phase 5 ${Date.now()}`);
      await byId(page, 'project-create').click();
      await expect(page).toHaveURL(/\/projects\/\d+\?tab=inputs$/);
      await byId(page, 'tab-files').click();
      await expect(page).toHaveURL(/tab=files/);
      await expect(byId(page, 'files-empty')).toBeVisible();
      await expect(byId(page, 'files-envelope')).toContainText('240 × 240 × 240');
    });

    let exportId = 0;
    await test.step('generate the files for the draft', async () => {
      const started = page.waitForResponse((r) => /\/api\/projects\/\d+\/exports$/.test(r.url()) && r.request().method() === 'POST');
      await byId(page, 'files-generate-button').click();
      const response = await started;
      expect(response.status()).toBe(202);
      exportId = ((await response.json()) as { id: number }).id;
      await expect(byId(page, 'files-result')).toBeVisible({ timeout: 60_000 });
      await expect(byId(page, 'files-result')).toHaveAttribute('data-export', String(exportId));
      await expect(byId(page, 'files-generate-button')).toHaveText('Generate files');
      const item = page.locator(`[data-testid="files-export-item"][data-id="${exportId}"]`);
      await expect(item).toHaveAttribute('data-status', 'done');
      await expect(item.getByTestId('files-export-status')).toContainText('Ready');
    });

    await test.step('every piece is ticked as fitting the envelope', async () => {
      await expect(byId(page, 'files-fit-all')).toContainText('All fit');
      const pieces = byId(page, 'files-piece');
      await expect(pieces).toHaveCount(2);
      for (let i = 0; i < 2; i += 1) {
        await expect(pieces.nth(i)).toHaveAttribute('data-fits', 'true');
        await expect(pieces.nth(i).getByTestId('files-piece-fit')).toContainText('Fits');
      }
      await expect(byId(page, 'files-piece-count')).toHaveAttribute('data-value', '2');
      await expect(byId(page, 'files-part')).toHaveCount(1);
      await expect(byId(page, 'files-part').first()).toContainText('Wing panel, right');
    });

    await test.step('downloads are grouped by kind, each saying what opens it', async () => {
      for (const group of ['print', 'cad', 'drawings', 'bom', 'notes']) {
        await expect(page.locator(`[data-testid="files-group"][data-group="${group}"]`)).toHaveCount(1);
      }
      const print = page.locator('[data-testid="files-group"][data-group="print"]');
      await print.locator('summary').click();
      await expect(print.locator('[data-testid="files-file"][data-kind="stl"]').first().getByTestId('files-opens-with')).toContainText(
        'Bambu Studio',
      );
      await expect(page.locator('[data-testid="files-file"][data-kind="step"]').first().getByTestId('files-opens-with')).toContainText('FreeCAD');
      await expect(page.locator('[data-testid="files-file"][data-kind="dxf"]').first().getByTestId('files-opens-with')).toContainText('LibreCAD');
      await expect(page.locator('[data-testid="files-file"][data-kind="csv"]').first().getByTestId('files-opens-with')).toContainText('LibreOffice');
      const opens = byId(page, 'files-opens-with');
      for (let i = 0; i < (await opens.count()); i += 1) {
        expect(((await opens.nth(i).textContent()) ?? '').trim().length).toBeGreaterThan(10);
      }
    });

    await test.step('download the ZIP and the BOM', async () => {
      const href = await byId(page, 'files-zip').getAttribute('href');
      expect(href).toBe(`/api/exports/${exportId}/zip`);
      const zip = await page.request.get(href!);
      expect(zip.ok()).toBe(true);
      expect(zip.headers()['content-type']).toContain('application/zip');
      const body = await zip.body();
      expect([...body.subarray(0, 4)]).toEqual([0x50, 0x4b, 0x03, 0x04]);

      const [download] = await Promise.all([page.waitForEvent('download'), byId(page, 'files-zip').click()]);
      expect(download.suggestedFilename()).toMatch(/\.zip$/);
      const saved = await download.path();
      const head = fs.readFileSync(saved).subarray(0, 4);
      expect([...head]).toEqual([0x50, 0x4b, 0x03, 0x04]);

      const bomLink = page.locator('[data-testid="files-file"][data-path="bom.csv"]').getByTestId('files-file-link');
      const bom = await page.request.get((await bomLink.getAttribute('href'))!);
      expect(bom.ok()).toBe(true);
      expect(bom.headers()['content-type']).toContain('text/csv');
      expect(await bom.text()).toContain('item');
    });

    await test.step('preview a piece on the bed outline with its envelope', async () => {
      const piece = page.locator('[data-testid="files-piece"][data-piece="wing_right_02of02"]');
      const mesh = page.waitForResponse((r) => r.url().includes('/pieces/wing_right_02of02/mesh'));
      await piece.getByTestId('files-piece-preview').click();
      expect((await mesh).ok()).toBe(true);
      const dialog = byId(page, 'piece-preview');
      await expect(dialog).toBeVisible();
      await expect(dialog).toContainText('Wing R 2/2');
      await expect(byId(page, 'piece-preview-3d')).toBeVisible();
      await expect(byId(page, 'piece-preview-size')).toContainText('100 × 100 × 100');
      await expect(byId(page, 'piece-preview-envelope')).toContainText('240 × 240 × 240');
      await expect(byId(page, 'piece-preview-fit')).toContainText('Yes');
      await expect(byId(page, 'piece-preview-orientation')).toContainText('leading edge down');
      await byId(page, 'piece-preview-close').click();
      await expect(dialog).toBeHidden();
    });

    await test.step('delete the export', async () => {
      const item = page.locator(`[data-testid="files-export-item"][data-id="${exportId}"]`);
      await item.getByTestId('files-export-delete').click();
      const confirm = byId(page, 'files-tab').getByTestId('confirm-dialog');
      await expect(confirm).toBeVisible();
      await expect(confirm).toContainText('removed from the server');
      await confirm.getByTestId('confirm-ok').click();
      await expect(item).toHaveCount(0);
      await expect(byId(page, 'files-result')).toHaveCount(0);
      await expect(byId(page, 'files-empty')).toBeVisible();
      const gone = await page.request.get(`/api/exports/${exportId}`);
      expect(gone.status()).toBe(404);
    });
  });

  test('the Files tab works at phone width', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto('/login');
    await byId(page, 'login-password').fill(TEST_PASSWORD);
    await byId(page, 'login-submit').click();
    await expect(page).not.toHaveURL(/\/login/);
    await byId(page, 'projects-new').click();
    await byId(page, 'project-name').fill(`Phase 5 phone ${Date.now()}`);
    await byId(page, 'project-create').click();
    await expect(page).toHaveURL(/\/projects\/\d+\?tab=inputs$/);
    const url = new URL(page.url());
    await page.goto(`${url.pathname}?tab=files`);
    await byId(page, 'files-generate-button').click();
    await expect(byId(page, 'files-result')).toBeVisible({ timeout: 60_000 });
    await expect(byId(page, 'files-zip')).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
