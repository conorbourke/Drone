/**
 * Phase 4 acceptance (docs/phases/PHASE4.md section 5): the parts catalogue is seeded on a fresh
 * database (migration 0004); opening Parts for the default design gives a complete list with
 * supplier links and a total against the €5,000 budget; replacing a part (the ESC) updates the
 * totals and the design's Tier 1 mass estimate; a price check runs through the worker (the
 * canned answer in e2e/fixtures/supplier_fake.json) and a second one within the hour is refused
 * with a plain message.
 */
import { expect, test, type Locator, type Page } from '@playwright/test';
import { TEST_PASSWORD } from '../playwright.config';

const byId = (page: Page, id: string): Locator => page.getByTestId(id);
const line = (page: Page, role: string): Locator => page.locator(`[data-testid="parts-line"][data-role="${role}"]`);
const num = async (locator: Locator, attr = 'data-value'): Promise<number> => Number(await locator.getAttribute(attr));

test.describe('Phase 4 acceptance', () => {
  test('parts list, budget, replace a part, price check, catalogue', async ({ page }) => {
    test.setTimeout(180_000);

    await test.step('sign in; the catalogue is seeded on a fresh database', async () => {
      await page.goto('/login');
      await byId(page, 'login-password').fill(TEST_PASSWORD);
      await byId(page, 'login-submit').click();
      await expect(page).not.toHaveURL(/\/login/);
      const response = await page.request.get('/api/parts');
      expect(response.ok()).toBe(true);
      const parts = (await response.json()) as Array<{ category: string; listings: unknown[]; source: string }>;
      expect(parts.length).toBeGreaterThanOrEqual(50);
      const categories = new Set(parts.map((p) => p.category));
      for (const c of ['motor', 'propeller', 'esc', 'servo', 'battery', 'cell', 'autopilot', 'gps', 'radio', 'telemetry', 'carbon_tube']) {
        expect(categories.has(c)).toBe(true);
      }
      expect(parts.filter((p) => p.listings.length > 0).length).toBeGreaterThan(30);
    });

    await test.step('create a project with the default design and open Parts', async () => {
      await byId(page, 'projects-new').click();
      await byId(page, 'project-name').fill(`Phase 4 ${Date.now()}`);
      await byId(page, 'project-create').click();
      await expect(page).toHaveURL(/\/projects\/\d+\?tab=inputs$/);
      await byId(page, 'tab-parts').click();
      await expect(page).toHaveURL(/tab=parts/);
    });

    await test.step('a complete list with supplier links and a total against €5,000', async () => {
      // A new draft has nothing stored, so the list is recomputed once on open.
      await expect(line(page, 'lift_motor')).toBeVisible({ timeout: 30_000 });
      await expect(byId(page, 'parts-updating')).toHaveCount(0, { timeout: 30_000 });
      for (const role of ['lift_motor', 'lift_prop', 'esc', 'tilt_servo', 'battery', 'autopilot', 'gps', 'radio', 'telemetry', 'spar_tube', 'boom_tube']) {
        await expect(line(page, role)).toHaveCount(1);
      }
      await expect(byId(page, 'parts-unfilled')).toHaveCount(0);
      await expect(byId(page, 'parts-consumables')).toBeVisible();
      for (const system of ['propulsion', 'energy', 'flight_control', 'structure', 'consumables']) {
        await expect(page.locator(`[data-testid="parts-system"][data-system="${system}"]`)).toBeVisible();
      }
      const links = byId(page, 'parts-supplier-link');
      expect(await links.count()).toBeGreaterThanOrEqual(8);
      for (let i = 0; i < (await links.count()); i += 1) {
        await expect(links.nth(i)).toHaveAttribute('href', /^https:\/\//);
        await expect(links.nth(i)).toHaveAttribute('target', '_blank');
      }
      await expect(byId(page, 'parts-supplier-country').first()).toHaveAttribute('data-country', /^(IE|UK)$/);
      await expect(byId(page, 'parts-last-checked').first()).toContainText(/checked .*2026/);
      await expect(byId(page, 'parts-link-status').first()).toHaveAttribute('data-status', /working|unchecked|broken|stale/);
      await expect(line(page, 'lift_motor').locator('[data-testid="parts-flag"][data-code="unverified"]')).toBeVisible();
      await expect(line(page, 'lift_motor').getByTestId('parts-unverified-source')).toHaveAttribute('href', /^https:\/\//);

      await expect(byId(page, 'parts-budget')).toHaveAttribute('data-value', '5000');
      await expect(byId(page, 'parts-totals')).toContainText('€5,000.00');
      const cost = await num(byId(page, 'parts-total-cost'));
      expect(cost).toBeGreaterThan(500);
      await expect(byId(page, 'parts-budget-bar')).toHaveAttribute('role', 'meter');
      await expect(byId(page, 'parts-budget-status')).toHaveAttribute('data-status', /under|near|over/);
      await expect(byId(page, 'parts-budget-message')).toContainText('5,000');
      await expect(byId(page, 'parts-uk-note')).toContainText(/VAT/);
      expect(await byId(page, 'parts-upgrade').count()).toBeGreaterThan(0);

      await line(page, 'lift_motor').getByTestId('parts-reasoning-toggle').click();
      await expect(line(page, 'lift_motor').getByTestId('parts-reasoning')).toContainText(/thrust-to-weight/);
    });

    let massBefore = 0;
    await test.step('the Design tab estimate uses the selected parts', async () => {
      await byId(page, 'tab-design').click();
      await expect(byId(page, 'estimates-parts-masses')).toContainText('ESCs');
      massBefore = await num(byId(page, 'estimate-mass'));
      expect(massBefore).toBeGreaterThan(2);
      await byId(page, 'tab-parts').click();
      await expect(line(page, 'esc')).toBeVisible({ timeout: 30_000 });
    });

    await test.step('replace the ESC: locked, totals and the design mass update', async () => {
      const esc = line(page, 'esc');
      const oldPart = await esc.getAttribute('data-part-id');
      const costBefore = await num(byId(page, 'parts-total-cost'));
      const partsMassBefore = await num(byId(page, 'parts-mass-total'));
      await esc.getByTestId('parts-replace').click();
      const dialog = byId(page, 'parts-replace-dialog');
      await expect(dialog).toBeVisible();
      const feasible = dialog.locator('[data-testid="parts-alternative"][data-feasible="true"]');
      expect(await feasible.count()).toBeGreaterThan(0);
      await expect(dialog.locator('[data-testid="parts-alternative"]').first()).toContainText(/mass [+−]/);
      const newPart = await feasible.first().getAttribute('data-part-id');
      expect(newPart).not.toBe(oldPart);
      await feasible.first().getByTestId('parts-alternative-choose').click();
      await expect(dialog).toBeHidden();
      await expect(esc).toHaveAttribute('data-part-id', newPart!);
      await expect(esc).toHaveAttribute('data-locked', 'true');
      await expect(esc.getByTestId('parts-locked')).toBeVisible();
      await expect.poll(async () => num(byId(page, 'parts-total-cost'))).not.toBe(costBefore);
      await expect.poll(async () => num(byId(page, 'parts-mass-total'))).not.toBe(partsMassBefore);

      await byId(page, 'tab-design').click();
      await expect.poll(async () => num(byId(page, 'estimate-mass'))).not.toBe(massBefore);
      await byId(page, 'tab-parts').click();
      await expect(line(page, 'esc')).toHaveAttribute('data-locked', 'true', { timeout: 30_000 });
    });

    await test.step('unlock hands the ESC back to the engine', async () => {
      await line(page, 'esc').getByTestId('parts-unlock').click();
      await expect(line(page, 'esc')).toHaveAttribute('data-locked', 'false');
    });

    await test.step('check prices now: queued, done, then refused within the hour', async () => {
      const motor = line(page, 'lift_motor');
      await motor.getByTestId('parts-refresh').click();
      await expect(motor.getByTestId('parts-refresh-status')).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
      await expect(motor.getByTestId('parts-refresh-message')).toContainText(/listing/i);
      await motor.getByTestId('parts-refresh').click();
      await expect(motor.getByTestId('parts-refresh-message')).toContainText(/Checked within the last hour; try again in \d+ minutes?/);
    });

    await test.step('the catalogue lists the seeded parts', async () => {
      await byId(page, 'parts-catalogue').locator('summary').click();
      await expect(byId(page, 'parts-table')).toBeVisible();
      expect(await byId(page, 'part-row').count()).toBeGreaterThanOrEqual(50);
    });
  });
});
