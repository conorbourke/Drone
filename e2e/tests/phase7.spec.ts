/**
 * Phase 7 acceptance (docs/phases/PHASE7.md section 3): open a 24 kg design (the default
 * prototype scaled to the final mass, e2e/fixtures/design_24kg.json) and see the full-scale
 * checks with the motor-out result; generate moulds for the nose, fuselage and wing-root
 * fairing and see the tiles and the draft report, the downloads grouped with what opens them,
 * the ZIP, a tile preview on the printer bed, and delete the set.
 *
 * The backend runs with MOULD_FAKE_GENERATOR (playwright.config.ts), so a mould set is the small
 * fake set of backend/tests/export_fakes.py:generate_moulds, written at once.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { TEST_PASSWORD } from '../playwright.config';

const byId = (page: Page, id: string): Locator => page.getByTestId(id);
const here = path.dirname(fileURLToPath(import.meta.url));
const DESIGN_24KG = JSON.parse(fs.readFileSync(path.join(here, '..', 'fixtures', 'design_24kg.json'), 'utf-8')) as {
  parameters: unknown;
  mission: unknown;
};

async function signInWith24kgProject(page: Page, name: string): Promise<number> {
  await page.goto('/login');
  await byId(page, 'login-password').fill(TEST_PASSWORD);
  await byId(page, 'login-submit').click();
  await expect(page).not.toHaveURL(/\/login/);
  await byId(page, 'projects-new').click();
  await byId(page, 'project-name').fill(name);
  await byId(page, 'project-create').click();
  await expect(page).toHaveURL(/\/projects\/\d+\?tab=inputs$/);
  const projectId = Number(/\/projects\/(\d+)/.exec(page.url())![1]);
  const put = await page.request.put(`/api/projects/${projectId}/draft`, {
    data: DESIGN_24KG,
    headers: { 'X-Requested-With': 'fetch' },
  });
  expect(put.ok()).toBe(true);
  // Reload so the workspace shows the 24 kg draft, then open the Full scale tab.
  await page.goto(`/projects/${projectId}?tab=fullscale`);
  await expect(byId(page, 'fullscale-tab')).toBeVisible();
  return projectId;
}

test.describe('Phase 7 acceptance', () => {
  test('full-scale checks, moulds with tiles and draft report, downloads, ZIP, preview, delete', async ({ page }) => {
    test.setTimeout(120_000);
    let projectId = 0;

    await test.step('sign in and open a 24 kg design on the Full scale tab', async () => {
      projectId = await signInWith24kgProject(page, `Phase 7 ${Date.now()}`);
      await expect(byId(page, 'tab-fullscale')).toHaveAttribute('aria-current', 'page');
      await expect(byId(page, 'mtow-banner')).toBeVisible(); // 24 kg target: the mass banner shows
      await expect(byId(page, 'moulds-empty')).toBeVisible();
    });

    await test.step('run the full-scale checks and see the motor-out result', async () => {
      const answered = page.waitForResponse((r) => r.url().endsWith(`/api/projects/${projectId}/fullscale`) && r.request().method() === 'POST');
      await byId(page, 'fullscale-run').click();
      expect((await answered).status()).toBe(200);
      await expect(byId(page, 'fullscale-result')).toBeVisible({ timeout: 30_000 });
      const checks = byId(page, 'fullscale-checks');
      for (const key of ['fullscale.motor_out', 'fullscale.mtow', 'fullscale.spar_tube', 'fullscale.boom_tube', 'fullscale.landing_gear', 'fullscale.battery_current']) {
        await expect(checks.locator(`[data-check-key="${key}"]`)).toHaveCount(1);
      }
      // Phase 3 checks are there too.
      await expect(checks.locator('[data-check-key="hover_thrust_to_weight"]')).toHaveCount(1);
      const motorOut = byId(page, 'fullscale-motor-out');
      await expect(motorOut).toBeVisible();
      await expect(byId(page, 'fullscale-motor-out-case')).toHaveCount(8);
      await expect(byId(page, 'fullscale-motor-out-worst')).toContainText('Worst case:');
      await expect(byId(page, 'fullscale-x8')).toBeVisible();
      const level = await motorOut.getAttribute('data-level');
      if (level === 'fail') await expect(byId(page, 'fullscale-x8')).toContainText('octocopter');
      await expect(byId(page, 'fullscale-structure')).toContainText('Tube that would do');
      await expect(byId(page, 'fullscale-layup-total')).not.toHaveText('—');
      await expect(byId(page, 'fullscale-gear')).toContainText('g');
      await expect(byId(page, 'fullscale-battery')).toContainText('Hover');
      await expect(byId(page, 'fullscale-a3')).toContainText('IAA');
      await expect(byId(page, 'fullscale-mass')).toContainText('kg');
    });

    let mouldId = 0;
    await test.step('generate moulds for the nose, fuselage and wing-root fairing', async () => {
      for (const key of ['nose', 'fuselage', 'wing_root_fairing']) await expect(byId(page, `moulds-part-${key}`)).toBeChecked();
      const started = page.waitForResponse((r) => /\/api\/projects\/\d+\/moulds$/.test(r.url()) && r.request().method() === 'POST');
      await byId(page, 'moulds-generate').click();
      const response = await started;
      expect(response.status()).toBe(202);
      const body = (await response.json()) as { id: number; mould_parts: string[] };
      mouldId = body.id;
      expect(body.mould_parts).toEqual(['nose', 'fuselage', 'wing_root_fairing']);
      await expect(byId(page, 'moulds-result')).toBeVisible({ timeout: 60_000 });
      await expect(byId(page, 'moulds-result')).toHaveAttribute('data-mould', String(mouldId));
      await expect(byId(page, 'moulds-generate')).toHaveText('Generate moulds');
      const item = page.locator(`[data-testid="moulds-item"][data-id="${mouldId}"]`);
      await expect(item).toHaveAttribute('data-status', 'done');
      await expect(item.getByTestId('moulds-item-status')).toContainText('Ready: 8 tiles');
    });

    await test.step('see the tiles and the draft report per part', async () => {
      await expect(byId(page, 'moulds-fit-all')).toContainText('All fit');
      await expect(byId(page, 'moulds-tile-count')).toHaveAttribute('data-value', '8');
      await expect(byId(page, 'moulds-part')).toHaveCount(3);
      const tiles = byId(page, 'moulds-tile');
      await expect(tiles).toHaveCount(8);
      for (let i = 0; i < 8; i += 1) await expect(tiles.nth(i)).toHaveAttribute('data-fits', 'true');
      const nose = page.locator('[data-testid="moulds-part"][data-part="nose"]');
      await expect(nose.getByTestId('moulds-half')).toHaveCount(2);
      await expect(nose.getByTestId('moulds-flagged')).toContainText('1 face flagged');
      await expect(nose.getByTestId('moulds-draft-text')).toContainText('less than 2° of draft');
      const fairing = page.locator('[data-testid="moulds-part"][data-part="wing_root_fairing"]');
      await expect(fairing.getByTestId('moulds-draft-text')).toContainText('Every face has at least 2°');
      await nose.getByTestId('moulds-assembly').locator('summary').click();
      await expect(nose.getByTestId('moulds-assembly')).toContainText('Print all 2 tiles');
    });

    await test.step('downloads are grouped by type, each saying what opens it', async () => {
      for (const group of ['stl', '3mf', 'step', 'pdf']) {
        await expect(page.locator(`[data-testid="moulds-group"][data-group="${group}"]`)).toHaveCount(1);
      }
      const stl = page.locator('[data-testid="moulds-group"][data-group="stl"]');
      await expect(stl.getByTestId('moulds-opens-with')).toContainText('Bambu Studio');
      await expect(page.locator('[data-testid="moulds-group"][data-group="step"]').getByTestId('moulds-opens-with')).toContainText('FreeCAD');
      await expect(page.locator('[data-testid="moulds-group"][data-group="pdf"]').getByTestId('moulds-opens-with')).toContainText('PDF reader');
      const pdf = page.locator('[data-testid="moulds-file"][data-kind="pdf"]').first().locator('a');
      const file = await page.request.get((await pdf.getAttribute('href'))!);
      expect(file.ok()).toBe(true);
      expect(file.headers()['content-type']).toContain('application/pdf');
    });

    await test.step('download the ZIP', async () => {
      const href = await byId(page, 'moulds-zip').getAttribute('href');
      expect(href).toBe(`/api/moulds/${mouldId}/zip`);
      const [download] = await Promise.all([page.waitForEvent('download'), byId(page, 'moulds-zip').click()]);
      expect(download.suggestedFilename()).toMatch(/moulds-\d+\.zip$/);
      const head = fs.readFileSync(await download.path()).subarray(0, 4);
      expect([...head]).toEqual([0x50, 0x4b, 0x03, 0x04]);
    });

    await test.step('preview a tile on the printer bed', async () => {
      const tile = page.locator('[data-testid="moulds-tile"][data-tile="FUS-L-02of02"]');
      const mesh = page.waitForResponse((r) => r.url().includes('/tiles/FUS-L-02of02/mesh'));
      await tile.getByTestId('moulds-tile-preview').click();
      expect((await mesh).ok()).toBe(true);
      const dialog = byId(page, 'tile-preview');
      await expect(dialog).toBeVisible();
      await expect(dialog).toContainText('FUS-L 2/2');
      await expect(byId(page, 'piece-preview-3d')).toBeVisible();
      await expect(byId(page, 'tile-preview-size')).toContainText('80 × 80 × 80');
      await expect(byId(page, 'tile-preview-fit')).toContainText('Yes');
      await expect(byId(page, 'tile-preview-orientation')).toContainText('back down');
      await byId(page, 'tile-preview-close').click();
      await expect(dialog).toBeHidden();
    });

    await test.step('delete the mould set', async () => {
      const item = page.locator(`[data-testid="moulds-item"][data-id="${mouldId}"]`);
      await item.getByTestId('moulds-item-delete').click();
      const confirm = byId(page, 'fullscale-tab').getByTestId('confirm-dialog');
      await expect(confirm).toBeVisible();
      await expect(confirm).toContainText('removed from the server');
      await confirm.getByTestId('confirm-ok').click();
      await expect(item).toHaveCount(0);
      await expect(byId(page, 'moulds-result')).toHaveCount(0);
      await expect(byId(page, 'moulds-empty')).toBeVisible();
      expect((await page.request.get(`/api/moulds/${mouldId}`)).status()).toBe(404);
    });
  });

  test('the Full scale tab works at phone width', async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 375, height: 800 });
    await signInWith24kgProject(page, `Phase 7 phone ${Date.now()}`);
    await byId(page, 'fullscale-run').click();
    await expect(byId(page, 'fullscale-result')).toBeVisible({ timeout: 30_000 });
    await byId(page, 'moulds-generate').click();
    await expect(byId(page, 'moulds-result')).toBeVisible({ timeout: 60_000 });
    await expect(byId(page, 'moulds-zip')).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
