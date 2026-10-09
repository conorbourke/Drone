/**
 * Phase 6 acceptance (docs/phases/PHASE6.md section 7): a sample ArduPilot log is parsed and
 * compared against its design's predictions. Sign in, create a project, open Flight data, see
 * the logging guide, load the bundled SITL sample flight, see its phase timeline and the
 * predicted-versus-measured table, apply the calibration (and see what it changes), undo and
 * re-apply it, upload a second log through the file picker, save built weights, check the
 * phone layout, and delete the logs.
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { TEST_PASSWORD } from '../playwright.config';

const here = path.dirname(fileURLToPath(import.meta.url));
const SYNTHETIC_LOG = path.resolve(here, '..', '..', 'backend', 'tests', 'fixtures', 'logs', 'synthetic_quadplane.bin');

const byId = (page: Page, id: string): Locator => page.getByTestId(id);
const PHASES = ['takeoff_hover', 'transition', 'cruise', 'back_transition', 'landing_hover'];

test.describe('Phase 6 acceptance', () => {
  test('sample flight: phases, comparison, calibration, built weights, delete', async ({ page }) => {
    test.setTimeout(240_000);

    await test.step('sign in and create a project', async () => {
      await page.goto('/login');
      await byId(page, 'login-password').fill(TEST_PASSWORD);
      await byId(page, 'login-submit').click();
      await expect(page).not.toHaveURL(/\/login/);
      await byId(page, 'projects-new').click();
      await byId(page, 'project-name').fill(`Phase 6 ${Date.now()}`);
      await byId(page, 'project-create').click();
      await expect(page).toHaveURL(/\/projects\/\d+\?tab=inputs$/);
      await byId(page, 'tab-flight').click();
      await expect(page).toHaveURL(/tab=flight/);
    });

    await test.step('the logging guide states LOG_BITMASK and why', async () => {
      await byId(page, 'flight-guide').locator('summary').first().click();
      await expect(byId(page, 'flight-logbitmask')).toContainText('11199');
      await expect(byId(page, 'flight-guide')).toContainText('LOG_DISARMED');
      await expect(byId(page, 'flight-guide')).toContainText('BATT_MONITOR');
      await expect(byId(page, 'flight-logs-empty')).toBeVisible();
    });

    await test.step('load the sample flight and see it read', async () => {
      await byId(page, 'flight-load-sample').click();
      const item = byId(page, 'flight-log-item').first();
      await expect(item).toContainText('sitl_quadplane.bin');
      await expect(item).toHaveAttribute('data-status', 'done', { timeout: 90_000 });
      await expect(byId(page, 'flight-result')).toBeVisible();
    });

    await test.step('the phase timeline shows hover, transition, cruise and landing', async () => {
      const phases = byId(page, 'flight-timeline').getByTestId('flight-phase');
      await expect(phases).toHaveCount(5);
      for (const [i, key] of PHASES.entries()) {
        await expect(phases.nth(i)).toHaveAttribute('data-phase', key);
      }
      await expect(byId(page, 'flight-phase-table')).toContainText('Take-off hover');
    });

    await test.step('the predicted-versus-measured table has explained rows', async () => {
      const table = byId(page, 'flight-comparison');
      await expect(table).toBeVisible();
      await expect(table).toContainText('Compared with');
      const hover = page.locator('[data-testid="flight-comparison-row"][data-key="hover_power"]');
      await expect(hover).toBeVisible();
      await expect(hover).toContainText('W');
      const cruise = page.locator('[data-testid="flight-comparison-row"][data-key="cruise_power"]');
      await expect(cruise).toHaveAttribute('data-status', /inside|outside/);
      // every row carries an explanation button
      const rows = table.locator('[data-testid="flight-comparison-row"]');
      expect(await rows.count()).toBeGreaterThanOrEqual(5);
      await expect(rows.first().getByRole('button')).toHaveCount(1);
      await expect(byId(page, 'flight-charts')).toBeVisible();
      await expect(byId(page, 'flight-chart-power')).toBeVisible();
    });

    await test.step('apply the calibration, see what changes, undo and apply again', async () => {
      const panel = byId(page, 'calibration-panel');
      await expect(panel.locator('[data-testid="calibration-factor"][data-name="hover_power"]')).toHaveAttribute(
        'data-applicable',
        'true',
      );
      await expect(byId(page, 'calibration-applied')).toHaveText('Not applied');
      await byId(page, 'calibration-preview-button').click();
      const hoverPreview = page.locator('[data-testid="calibration-preview-row"][data-key="hover_power"]');
      await expect(hoverPreview).toBeVisible({ timeout: 60_000 });
      await byId(page, 'calibration-apply').click();
      await expect(byId(page, 'calibration-applied')).toContainText('Applied · 1 flight');
      await byId(page, 'calibration-undo').click();
      await expect(byId(page, 'calibration-applied')).toHaveText('Not applied');
      await byId(page, 'calibration-apply').click();
      await expect(byId(page, 'calibration-applied')).toContainText('Applied');
      // analyses queued from now on carry the calibration
      const projectId = Number(new URL(page.url()).pathname.split('/')[2]);
      const state = await (await page.request.get(`/api/projects/${projectId}/calibration`)).json();
      expect(Object.keys(state.applied.factors)).toContain('hover_power');
    });

    await test.step('upload a log through the file picker', async () => {
      await byId(page, 'flight-upload-mass').fill('3.3');
      await byId(page, 'flight-upload-input').setInputFiles(SYNTHETIC_LOG);
      const item = page.locator('[data-testid="flight-log-item"]', { hasText: 'synthetic_quadplane.bin' });
      await expect(item).toBeVisible({ timeout: 30_000 });
      await expect(item).toHaveAttribute('data-status', 'done', { timeout: 90_000 });
      await expect(byId(page, 'flight-result')).toContainText('synthetic_quadplane.bin');
      await expect(byId(page, 'flight-comparison')).toContainText('3.3 kg');
    });

    await test.step('save built weights and see the structural factor', async () => {
      const wing = byId(page, 'built-weight-wing_structure');
      await expect(wing).toBeVisible({ timeout: 60_000 });
      await wing.fill('350');
      await byId(page, 'built-weight-booms').fill('120');
      await byId(page, 'built-weights-save').click();
      await expect(byId(page, 'built-weights-structural')).toContainText('×', { timeout: 30_000 });
      await expect(byId(page, 'built-weights-totals')).toContainText('2 of');
      await page.reload();
      await expect(byId(page, 'built-weight-wing_structure')).toHaveValue('350', { timeout: 60_000 });
    });

    await test.step('works at phone width without sideways scrolling', async () => {
      await page.setViewportSize({ width: 390, height: 844 });
      await expect(byId(page, 'flight-tab')).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(1);
      await page.setViewportSize({ width: 1280, height: 800 });
    });

    await test.step('delete the logs', async () => {
      const items = byId(page, 'flight-log-item');
      await expect(items).toHaveCount(2);
      for (let remaining = 2; remaining > 0; remaining -= 1) {
        await items.first().getByTestId('flight-log-delete').click();
        await byId(page, 'confirm-ok').click();
        await expect(items).toHaveCount(remaining - 1);
      }
      await expect(byId(page, 'flight-logs-empty')).toBeVisible();
      await expect(byId(page, 'flight-result')).toHaveCount(0);
    });
  });
});
