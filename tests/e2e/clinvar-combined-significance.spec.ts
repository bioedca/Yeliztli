/**
 * Issue #687 - combined ClinVar "Pathogenic/Likely pathogenic" labels must
 * use the pathogenic visual treatment on module cards instead of falling
 * through to the neutral gray default.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const CARRIER_VARIANTS = {
  total: 1,
  genes_with_findings: ['HBB'],
  items: [
    {
      rsid: 'rs334',
      gene_symbol: 'HBB',
      genotype: 'A/T',
      zygosity: 'het',
      clinvar_significance: 'Pathogenic/Likely pathogenic',
      clinvar_accession: 'VCV000015333',
      clinvar_review_stars: 2,
      clinvar_conditions: 'Sickle cell disease',
      conditions: ['Sickle Cell Disease'],
      inheritance: 'AR',
      evidence_level: 4,
      cross_links: [],
      pmids: ['20301551'],
      notes: 'HBB carrier-panel example.',
    },
  ],
}

const CARRIER_DISCLAIMER = {
  title: 'About carrier status',
  text: 'Reproductive carrier screening reference text.',
  gene_notes: {},
}

test.describe('ClinVar combined pathogenic significance styling (#687)', () => {
  test('Carrier Status card renders combined P/LP as pathogenic red', async ({ page }) => {
    await page.route('**/api/analysis/carrier/variants**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(CARRIER_VARIANTS),
      })
    })
    await page.route('**/api/analysis/carrier/disclaimer**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(CARRIER_DISCLAIMER),
      })
    })

    await page.goto('/carrier-status?sample_id=1')
    await waitForReactHydration(page)

    const hbbCard = page.getByTestId('carrier-variant-card').filter({ hasText: 'HBB' })
    await expect(hbbCard).toBeVisible()
    await expect(hbbCard).toHaveClass(/bg-red-50/)
    await expect(hbbCard).toHaveClass(/border-red-200/)

    const significanceBadge = hbbCard.getByText('Pathogenic/Likely pathogenic')
    await expect(significanceBadge).toBeVisible()
    await expect(significanceBadge).toHaveClass(/bg-red-100/)
    await expect(significanceBadge).toHaveClass(/text-red-800/)
  })
})
