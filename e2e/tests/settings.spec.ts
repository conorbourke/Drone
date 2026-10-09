/**
 * Settings page: every value carries a plain-language explanation, invalid values are refused
 * with a readable message, valid values persist, and a backup can be made and downloaded.
 */
import { expect, test, type Page } from '@playwright/test';
import { TEST_PASSWORD } from '../playwright.config';

async function signIn(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-password').fill(TEST_PASSWORD);
  await page.getByTestId('login-submit').click();
  await expect(page).not.toHaveURL(/\/login/);
}

test.describe('Settings page', () => {
  test('explanations, validation, persistence and backups', async ({ page }) => {
    await signIn(page);
    await page.goto('/settings');
    await expect(page.getByTestId('settings-form')).toBeVisible();

    await test.step('a fresh database shows no warnings and every row has an explanation', async () => {
      await expect(page.getByTestId('settings-warnings')).toHaveCount(0);
      const rows = page.locator('[data-testid^="setting-row-"]');
      const count = await rows.count();
      expect(count).toBeGreaterThanOrEqual(12);
      for (let i = 0; i < count; i += 1) {
        await expect(rows.nth(i).locator('button.explain-button').first()).toBeVisible();
      }
      await rows.first().locator('button.explain-button').first().hover();
      await expect(page.getByRole('tooltip')).toBeVisible();
    });

    await test.step('an impossible value is refused with a plain message and not stored', async () => {
      await page.getByTestId('setting-limits.warn_mtow_kg').fill('29');
      await page.getByTestId('settings-save').click();
      await expect(page.getByText(/Mass limits/)).toBeVisible();
      await page.reload();
      await expect(page.getByTestId('setting-limits.warn_mtow_kg')).toHaveValue('23');
    });

    await test.step('a valid change is saved and survives a reload', async () => {
      await page.getByTestId('setting-limits.warn_mtow_kg').fill('22');
      await page.getByTestId('settings-save').click();
      await expect(page.getByText('Settings saved')).toBeVisible();
      await page.reload();
      await expect(page.getByTestId('setting-limits.warn_mtow_kg')).toHaveValue('22');
      // Put the default back so the workspace banner thresholds stay as documented.
      await page.getByTestId('setting-limits.warn_mtow_kg').fill('23');
      await page.getByTestId('settings-save').click();
      await expect(page.getByText('Settings saved')).toBeVisible();
    });

    await test.step('a backup can be made now and downloaded', async () => {
      await page.getByTestId('backup-now').click();
      const row = page.getByTestId('backup-row').first();
      await expect(row).toBeVisible();
      await expect(row.locator('a[download]')).toHaveAttribute(
        'href',
        /\/api\/system\/backups\/app-\d{8}-\d{6}\.db$/,
      );
    });
  });
});
