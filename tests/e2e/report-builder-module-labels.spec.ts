/**
 * Issue #1497 — Report Builder used its local display-name map and then
 * title-cased unknown module keys, so panel-only acronyms that already exist in
 * MODULE_META rendered as "Amd" instead of "AMD".
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const SUMMARY = {
  total_findings: 3,
  modules: [
    {
      module: 'amd',
      count: 1,
      max_evidence_level: 2,
      top_finding_text: 'AMD risk',
    },
    {
      module: 'research_panel',
      count: 2,
      max_evidence_level: 2,
      top_finding_text: 'Research risk',
    },
  ],
  high_confidence_findings: [],
}

test.describe('Report Builder module labels (#1497)', () => {
  test('uses canonical registry labels before humanizing module keys', async ({ page }) => {
    await page.route('**/api/analysis/findings/summary**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(SUMMARY),
      })
    })

    await page.goto('/reports?sample_id=1')
    await waitForReactHydration(page)

    await expect(page.getByRole('button', { name: 'AMD: 1 findings' })).toBeVisible()
    await expect(page.getByText('AMD', { exact: true })).toBeVisible()
    await expect(page.getByText('Amd', { exact: true })).toHaveCount(0)

    await expect(page.getByRole('button', { name: 'Research Panel: 2 findings' })).toBeVisible()
  })
})
