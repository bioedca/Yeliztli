/**
 * Issue #924 — the skin detail panel must preserve the backend's case-sensitive
 * MC1R allele class. The mild rs885479/R163Q class is lowercase `r`; rendering
 * it as uppercase `R` makes it look like a strong-R MC1R allele.
 */

import { expect, test } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

function jsonRoute(payload: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(payload) }
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
  mc1r_aggregate: null,
  cross_module: [],
  insufficient_data: [],
}

const PIGMENTATION_DETAIL = {
  pathway_id: 'pigmentation_uv',
  pathway_name: 'Pigmentation & UV Response',
  level: 'Moderate',
  evidence_level: 2,
  called_snps: 1,
  total_snps: 1,
  missing_snps: [],
  pmids: ['18366057'],
  snp_details: [
    {
      rsid: 'rs885479',
      gene: 'MC1R',
      variant_name: 'R163Q',
      genotype: 'AG',
      category: 'Moderate',
      effect_summary:
        "One copy of the R163Q 'r' (mild) allele. Modestly reduced MC1R signaling.",
      evidence_level: 2,
      recommendation: null,
      pmids: ['18366057'],
      mc1r_allele_class: 'r',
      coverage_note: null,
      insufficient_data_flag: false,
    },
  ],
}

test('skin detail panel keeps mild MC1R r allele badge lowercase', async ({ page }) => {
  await page.route('**/api/analysis/skin/pathways**', async (route) => {
    await route.fulfill(jsonRoute(PATHWAYS))
  })
  await page.route('**/api/analysis/skin/pathway/**', async (route) => {
    await route.fulfill(jsonRoute(PIGMENTATION_DETAIL))
  })

  await page.goto('/skin?sample_id=1')
  await waitForReactHydration(page)

  await page
    .getByRole('button', { name: /Pigmentation & UV Response/i })
    .first()
    .click()

  const panel = page.getByRole('dialog', {
    name: /Pigmentation & UV Response pathway details/i,
  })
  await expect(panel).toBeVisible()

  await expect(panel.getByText('MC1R r allele', { exact: true })).toBeVisible()
  await expect(panel.getByText('MC1R R allele', { exact: true })).toHaveCount(0)
})
