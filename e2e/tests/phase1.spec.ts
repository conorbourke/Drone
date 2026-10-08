/**
 * Phase 1 acceptance: the owner opens the URL, is sent to the login page, signs in, creates a
 * project and saves two versions. Every selector is a data-testid from docs/ARCHITECTURE.md.
 *
 * The flow is one story, so it runs as a single test with named steps; nothing sleeps, every
 * wait is an `expect` that polls the page.
 */
import { expect, test, type Locator, type Page } from '@playwright/test';
import { TEST_PASSWORD } from '../playwright.config';

const byId = (page: Page, id: string): Locator => page.getByTestId(id);
const versionItems = (page: Page): Locator => page.getByTestId('version-item');
const versionNumber = (page: Page, n: number): Locator =>
  page.locator(`[data-testid="version-item"][data-version-number="${n}"]`);

/** Wait until the autosave has landed so the server holds what the page shows. */
async function expectSaved(page: Page): Promise<void> {
  await expect(byId(page, 'draft-status')).toHaveText('Saved');
}

async function signIn(page: Page): Promise<void> {
  await byId(page, 'login-password').fill(TEST_PASSWORD);
  await byId(page, 'login-submit').click();
  await expect(page).not.toHaveURL(/\/login/);
}

test.describe('Phase 1 acceptance', () => {
  test('login, create a project, save two versions, restore, reload, log out', async ({ page }) => {
    const projectName = `Acceptance ${Date.now()}`;
    let projectId = 0;

    await test.step('opening the app redirects to the login page', async () => {
      await page.goto('/');
      await expect(page).toHaveURL(/\/login$/);
      await expect(byId(page, 'login-password')).toBeVisible();
      await expect(byId(page, 'login-error')).toHaveCount(0);
    });

    await test.step('a wrong password shows an error and stays on the login page', async () => {
      await byId(page, 'login-password').fill('not-the-password');
      await byId(page, 'login-submit').click();
      await expect(byId(page, 'login-error')).toBeVisible();
      await expect(byId(page, 'login-error')).toContainText(/wrong password/i);
      await expect(page).toHaveURL(/\/login$/);
    });

    await test.step('the correct password signs in and shows the projects list', async () => {
      await signIn(page);
      await expect(page).toHaveURL(/\/$/);
      await expect(byId(page, 'projects-new')).toBeVisible();
    });

    await test.step('create a project and land in its Inputs tab', async () => {
      await byId(page, 'projects-new').click();
      await byId(page, 'project-name').fill(projectName);
      await byId(page, 'project-description').fill('Created by the Playwright acceptance test.');
      await byId(page, 'project-create').click();
      await expect(page).toHaveURL(/\/projects\/\d+\?tab=inputs$/);
      projectId = Number(/\/projects\/(\d+)/.exec(page.url())?.[1]);
      expect(projectId).toBeGreaterThan(0);
      await expect(byId(page, 'tab-inputs')).toHaveAttribute('aria-current', 'page');
      await expect(byId(page, 'draft-basis')).toContainText(/not based on a saved version/i);
      await expectSaved(page);
      await expect(versionItems(page)).toHaveCount(0);
    });

    await test.step('set the target take-off mass; the draft autosaves', async () => {
      const mass = byId(page, 'field-target_takeoff_mass_kg');
      await expect(mass).toHaveValue('2.5');
      await mass.fill('3');
      await expect(byId(page, 'draft-status')).toHaveText('Unsaved changes');
      await expectSaved(page);
      await expect(mass).toHaveValue('3');
    });

    await test.step('labels and option notes come from the schema endpoint', async () => {
      // The mission form is built from /api/schema/mission; the layout select from /api/schema/design.
      await expect(page.getByLabel('Target take-off mass', { exact: true })).toHaveValue('3');
      const layout = byId(page, 'field-layout');
      await expect(layout).toHaveValue('front_tilt');
      await layout.selectOption('rear_tilt');
      await expect(page.getByText('Less common in ArduPilot; support to be confirmed in Phase 2').first()).toBeVisible();
      await layout.selectOption('front_tilt');
      await expectSaved(page);
      await expect(layout).toHaveValue('front_tilt');
    });

    await test.step('save version v1', async () => {
      await byId(page, 'versions-save').click();
      await byId(page, 'version-name').fill('v1');
      await byId(page, 'version-notes').fill('First snapshot: take-off mass 3 kg.');
      await byId(page, 'version-save-confirm').click();
      await expect(versionNumber(page, 1)).toBeVisible();
      await expect(versionNumber(page, 1)).toContainText('v1');
      await expect(versionNumber(page, 1)).toHaveAttribute('data-version-id', /^\d+$/);
      await expect(versionItems(page)).toHaveCount(1);
      await expect(byId(page, 'draft-basis')).toHaveText('Draft based on v1');
    });

    await test.step('change the wingspan on the Design tab and wait for Saved', async () => {
      await byId(page, 'tab-design').click();
      await expect(page).toHaveURL(/tab=design$/);
      await expect(byId(page, 'tab-design')).toHaveAttribute('aria-current', 'page');
      const span = byId(page, 'field-wing.span_mm');
      await expect(span).toHaveValue('1800');
      await expect(page.getByLabel('Wingspan', { exact: true })).toHaveValue('1800');
      await span.fill('2000');
      await expect(byId(page, 'draft-basis')).toHaveText('Draft based on v1 (modified)');
      await expectSaved(page);
      await expect(span).toHaveValue('2000');
    });

    await test.step('save version v2', async () => {
      await byId(page, 'versions-save').click();
      await byId(page, 'version-name').fill('v2');
      await byId(page, 'version-save-confirm').click();
      await expect(versionNumber(page, 2)).toBeVisible();
      await expect(byId(page, 'draft-basis')).toHaveText('Draft based on v2');
    });

    await test.step('both versions are listed with numbers 1 and 2, newest first', async () => {
      await expect(versionItems(page)).toHaveCount(2);
      await expect(versionItems(page).nth(0)).toHaveAttribute('data-version-number', '2');
      await expect(versionItems(page).nth(0)).toContainText('v2');
      await expect(versionItems(page).nth(1)).toHaveAttribute('data-version-number', '1');
      await expect(versionItems(page).nth(1)).toContainText('v1');
      const ids = await versionItems(page).evaluateAll((items) =>
        items.map((item) => item.getAttribute('data-version-id')),
      );
      expect(new Set(ids).size).toBe(2);
      expect(ids.every((id) => /^\d+$/.test(id ?? ''))).toBe(true);
    });

    await test.step('restore v1: the wingspan reverts to 1800 without a confirmation (draft is clean)', async () => {
      await versionNumber(page, 1).getByTestId('version-menu').click();
      await byId(page, 'version-restore').click();
      await expect(byId(page, 'field-wing.span_mm')).toHaveValue('1800');
      await expect(byId(page, 'draft-basis')).toHaveText('Draft based on v1');
      await expectSaved(page);
      await expect(byId(page, 'confirm-ok')).toHaveCount(0);
    });

    await test.step('restore with unsaved edits asks for confirmation first', async () => {
      const span = byId(page, 'field-wing.span_mm');
      await span.fill('1900');
      await expect(byId(page, 'draft-basis')).toHaveText('Draft based on v1 (modified)');
      await expectSaved(page);
      await versionNumber(page, 2).getByTestId('version-menu').click();
      await byId(page, 'version-restore').click();
      await expect(byId(page, 'confirm-ok')).toBeVisible();
      await byId(page, 'confirm-cancel').click();
      await expect(byId(page, 'confirm-ok')).toHaveCount(0);
      await expect(span).toHaveValue('1900');
      await versionNumber(page, 2).getByTestId('version-menu').click();
      await byId(page, 'version-restore').click();
      await byId(page, 'confirm-ok').click();
      await expect(span).toHaveValue('2000');
      await expect(byId(page, 'draft-basis')).toHaveText('Draft based on v2');
      await expectSaved(page);
    });

    await test.step('reloading keeps the login and the data', async () => {
      await page.reload();
      await expect(page).toHaveURL(new RegExp(`/projects/${projectId}\\?tab=design$`));
      await expect(byId(page, 'field-wing.span_mm')).toHaveValue('2000');
      await expect(byId(page, 'draft-basis')).toHaveText('Draft based on v2');
      await expect(versionItems(page)).toHaveCount(2);
      await expect(versionNumber(page, 1)).toContainText('v1');
      await expect(versionNumber(page, 2)).toContainText('v2');
      await byId(page, 'tab-inputs').click();
      await expect(byId(page, 'field-target_takeoff_mass_kg')).toHaveValue('3');
    });

    await test.step('the project appears in the list', async () => {
      await page.goto('/');
      const card = page.locator(`[data-testid="project-card"][data-project-id="${projectId}"]`);
      await expect(card).toBeVisible();
      await expect(card).toContainText(projectName);
      await expect(card).toContainText('2 versions');
    });

    await test.step('log out, then a protected route redirects to the login page', async () => {
      await byId(page, 'logout').click();
      await expect(page).toHaveURL(/\/login$/);
      const me = await page.request.get('/api/auth/me');
      expect(me.status()).toBe(401);
      await page.goto(`/projects/${projectId}?tab=design`);
      await expect(page).toHaveURL(/\/login$/);
      await expect(byId(page, 'login-password')).toBeVisible();
      await page.goto('/settings');
      await expect(page).toHaveURL(/\/login$/);
    });

    await test.step('signing in again returns to the route that was requested', async () => {
      await page.goto(`/projects/${projectId}?tab=design`);
      await expect(page).toHaveURL(/\/login$/);
      await signIn(page);
      await expect(page).toHaveURL(new RegExp(`/projects/${projectId}\\?tab=design$`));
      await expect(byId(page, 'field-wing.span_mm')).toHaveValue('2000');
    });
  });

  test('delete a project asks for confirmation and removes it', async ({ page }) => {
    const projectName = `Disposable ${Date.now()}`;
    await page.goto('/login');
    await signIn(page);
    await byId(page, 'projects-new').click();
    await byId(page, 'project-name').fill(projectName);
    await byId(page, 'project-create').click();
    await expect(page).toHaveURL(/\/projects\/\d+\?tab=inputs$/);
    const projectId = Number(/\/projects\/(\d+)/.exec(page.url())?.[1]);

    await page.goto('/');
    const card = page.locator(`[data-testid="project-card"][data-project-id="${projectId}"]`);
    await expect(card).toBeVisible();

    await card.getByTestId('project-delete').click();
    await expect(byId(page, 'confirm-ok')).toBeVisible();
    await byId(page, 'confirm-cancel').click();
    await expect(byId(page, 'confirm-ok')).toHaveCount(0);
    await expect(card).toBeVisible();

    await card.getByTestId('project-delete').click();
    await byId(page, 'confirm-ok').click();
    await expect(card).toHaveCount(0);
    const gone = await page.request.get(`/api/projects/${projectId}`);
    expect(gone.status()).toBe(404);
  });
});
