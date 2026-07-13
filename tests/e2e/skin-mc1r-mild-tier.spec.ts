/**
 * Issue #1759 — an R163Q-only MC1R aggregate has zero strong R alleles but is
 * still the distinct "Mild MC1R Variant" tier. Its summary card must not reuse
 * the emerald baseline treatment reserved for "Low UV Sensitivity".
 */

import { expect, test } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

function jsonRoute(payload: unknown) {
  return { status: 200, contentType: 'application/json', body: JSON.stringify(payload) }
}

const PATHWAYS = {
  items: [
    {
      pathway_id: 'pigmentation_uv',
      pathway_name: 'Pigmentation & UV Response',
      level: 'Moderate',
      evidence_level: 2,
      called_snps: 1,
      total_snps: 1,
      missing_snps: [],
      pmids: ['18366057'],
    },
  ],
  total: 1,
  mc1r_aggregate: {
    r_allele_count: 0,
    r_allele_rsids: [],
    total_mc1r_called: 1,
    risk_label: 'Mild MC1R Variant',
    risk_description: 'A mild-effect MC1R variant was detected.',
    evidence_level: 2,
    pmids: ['18366057'],
  },
  cross_module: [],
  insufficient_data: [],
}

test('mild MC1R tier uses a non-baseline palette across the summary card', async ({ page }) => {
  await page.route('**/api/analysis/skin/pathways**', async (route) => {
    await route.fulfill(jsonRoute(PATHWAYS))
  })

  await page.goto('/skin?sample_id=1')
  await waitForReactHydration(page)

  const summary = page.getByRole('region', { name: 'MC1R allele summary' })
  await expect(summary.getByText('Mild MC1R Variant', { exact: true })).toBeVisible()

  const card = summary.locator(':scope > div').first()
  await expect(card).toHaveClass(/bg-sky-50/)
  await expect(card).toHaveClass(/dark:bg-sky-950\/30/)
  await expect(card).toHaveClass(/dark:border-sky-800/)
  await expect(card).not.toHaveClass(/bg-emerald/)

  const countBadge = summary.getByText('0', { exact: true })
  await expect(countBadge).toHaveClass(/bg-sky-100/)
  await expect(countBadge).toHaveClass(/dark:bg-sky-900\/50/)
  await expect(countBadge).toHaveClass(/dark:text-sky-300/)
  await expect(countBadge).not.toHaveClass(/bg-emerald/)

  const labelBox = summary.getByText('Mild MC1R Variant', { exact: true }).locator('..')
  await expect(labelBox).toHaveClass(/bg-sky-100\/50/)
  await expect(labelBox).toHaveClass(/dark:bg-sky-900\/20/)
  await expect(labelBox).not.toHaveClass(/bg-emerald/)
})
