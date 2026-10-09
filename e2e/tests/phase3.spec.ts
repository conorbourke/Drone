/**
 * Phase 3 acceptance (docs/phases/PHASE3.md section 7): Analyse returns results, checks and
 * ranked recommendations; the validation report is viewable in the app; the assistant (the
 * scripted fake, backend/tests/fixtures/chat_fake_script.json) streams an answer quoting a tool
 * result and proposes a change; a recommendation is applied as a new version.
 *
 * The full analysis of the default draft takes about 30 s on one CPU (results after ~6-10 s, the
 * recommendation sweep after that), so this spec allows up to 120 s for each wait on it.
 */
import { expect, test, type Locator, type Page } from '@playwright/test';
import { TEST_PASSWORD } from '../playwright.config';

const byId = (page: Page, id: string): Locator => page.getByTestId(id);
const ANALYSIS_TIMEOUT = 120_000;

test.describe('Phase 3 acceptance', () => {
  test('analyse, validation, assistant and Try as new version', async ({ page }) => {
    test.setTimeout(360_000);

    await test.step('sign in, create a project and open the Design tab', async () => {
      await page.goto('/login');
      await byId(page, 'login-password').fill(TEST_PASSWORD);
      await byId(page, 'login-submit').click();
      await expect(page).not.toHaveURL(/\/login/);
      await byId(page, 'projects-new').click();
      await byId(page, 'project-name').fill(`Phase 3 ${Date.now()}`);
      await byId(page, 'project-create').click();
      await expect(page).toHaveURL(/\/projects\/\d+\?tab=inputs$/);
      await byId(page, 'tab-design').click();
      await expect(byId(page, 'analysis-panel')).toBeVisible();
      await expect(byId(page, 'analysis-empty')).toBeVisible();
    });

    await test.step('Analyse the default draft: progress, then results and checks', async () => {
      await expect(byId(page, 'analyse-source')).toHaveValue('draft');
      await byId(page, 'analyse-run').click();
      await expect(byId(page, 'analysis-progress')).toBeVisible();
      await expect(byId(page, 'analysis-results')).toBeVisible({ timeout: ANALYSIS_TIMEOUT });
      await expect(byId(page, 'analysis-source-info')).toContainText('the draft');
      const checks = byId(page, 'analysis-check');
      expect(await checks.count()).toBeGreaterThanOrEqual(8);
      await expect(page.locator('[data-testid="analysis-check"][data-check-key="hover_thrust_to_weight"]')).toHaveAttribute('data-level', /ok|warn|fail/);
      const endurance = Number(await byId(page, 'analysis-metric-endurance_cruise').getAttribute('data-value'));
      expect(endurance).toBeGreaterThan(5);
      await expect(byId(page, 'analysis-a3-note')).toContainText('IAA authorisation');
      await expect(byId(page, 'chart-transition-margin')).toBeVisible();
      await expect(byId(page, 'chart-mission-power')).toBeVisible();
      await expect(byId(page, 'chart-top-speed-margin')).toBeVisible();
      await expect(byId(page, 'analysis-transition')).toContainText(/From hover to \d+(\.\d)? m\/s/);
      await expect(byId(page, 'analysis-tier-compare').locator('tr[data-row="cruise_power"]')).toBeVisible();
      await expect(byId(page, 'analysis-stale')).toHaveCount(0);
    });

    await test.step('ranked recommendations arrive', async () => {
      const recs = byId(page, 'recommendation');
      await expect(recs.first()).toBeVisible({ timeout: ANALYSIS_TIMEOUT });
      await expect(recs.first()).toHaveAttribute('data-rank', '1');
      await expect(recs.first()).toContainText(/endurance \+\d/);
      await expect(byId(page, 'analysis-progress')).toHaveCount(0);
    });

    await test.step('apply a recommendation as a new version', async () => {
      await byId(page, 'recommendation-try').first().click();
      const item = byId(page, 'version-item');
      await expect(item).toHaveCount(1);
      await expect(item.first().locator('.version-name')).toHaveText(/^Rec: /);
      await expect(page.getByText(/Saved as v1/)).toBeVisible();
    });

    await test.step('the validation page opens from the analysis panel and shows the report', async () => {
      await byId(page, 'validation-link').click();
      await expect(page).toHaveURL(/\/validation$/);
      await expect(byId(page, 'validation-summary')).toBeVisible();
      await expect(byId(page, 'validation-source')).toHaveAttribute('data-source', 'snapshot');
      expect(await byId(page, 'validation-case').count()).toBeGreaterThan(10);
      await expect(page.locator('[data-testid="validation-group"][data-group="published_design"]')).toBeVisible();
      await expect(page.getByText(/±30 %/).first()).toBeVisible();
      await page.goBack();
      await expect(byId(page, 'analysis-panel')).toBeVisible();
    });

    await test.step('ask the assistant: streamed answer quoting the tool result, and a proposal', async () => {
      await expect(byId(page, 'assistant-input')).toBeEnabled();
      await byId(page, 'assistant-input').fill('What if I used a bigger battery?');
      await byId(page, 'assistant-send').click();
      await expect(byId(page, 'assistant-busy')).toBeVisible();
      const chip = byId(page, 'assistant-tool-chip').filter({ hasText: 'Running a quick analysis' });
      await expect(chip).toBeVisible({ timeout: ANALYSIS_TIMEOUT });
      const answer = page.locator('[data-testid="assistant-message"][data-role="assistant"]').last();
      // The figures come from the run_quick_analysis result, quoted with their ranges.
      await expect(answer).toContainText(/wing-flight endurance of \d+(\.\d+)? min \(\d+(\.\d+)?-\d+(\.\d+)? min\)/, { timeout: ANALYSIS_TIMEOUT });
      await expect(byId(page, 'assistant-proposal')).toBeVisible({ timeout: ANALYSIS_TIMEOUT });
      await expect(byId(page, 'assistant-busy')).toHaveCount(0, { timeout: ANALYSIS_TIMEOUT });
      await expect(chip).toHaveAttribute('data-state', 'done');
    });

    await test.step('the proposal can be tried as a new version too', async () => {
      await byId(page, 'assistant-proposal-try').click();
      await expect(byId(page, 'version-item')).toHaveCount(2);
      await expect(page.locator('[data-testid="version-item"][data-version-number="2"] .version-name')).toHaveText(/^Assistant: \+1000 mAh battery/);
    });

    await test.step('the conversation is kept and can be cleared', async () => {
      await page.reload();
      await expect(page.locator('[data-testid="assistant-message"][data-role="user"]')).toHaveCount(1);
      await byId(page, 'assistant-clear').click();
      await byId(page, 'confirm-ok').click();
      await expect(byId(page, 'assistant-message')).toHaveCount(0);
    });

    await test.step('editing the draft marks the analysis stale, with a re-run button', async () => {
      await expect(byId(page, 'analysis-results')).toBeVisible();
      await expect(byId(page, 'analysis-stale')).toHaveCount(0);
      await byId(page, 'field-wing.span_mm').fill('1850');
      await expect(byId(page, 'draft-status')).toHaveText('Saved');
      await expect(byId(page, 'analysis-stale')).toBeVisible();
      await expect(byId(page, 'analysis-rerun')).toBeEnabled();
    });
  });
});
