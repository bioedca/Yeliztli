/**
 * Issue #940 — PRS cards dropped backend-authored ancestry caveats when
 * `ancestry_mismatch` was false. That is the normal "ancestry inference has not
 * been run" state, so the warning text itself must drive rendering.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const ANCESTRY_NOT_RUN =
  'Ancestry inference has not been run. PRS accuracy depends on the match between your ancestry and the study population.'

const SHARED_PRS = {
  name: 'Heel eBMD',
  calibrated: true,
  percentile: 55,
  snps_used: 1100,
  snps_total: 1200,
  coverage_fraction: 0.92,
  is_sufficient: true,
  source_study: 'Graham et al. 2021',
  source_pmid: '33462484',
  pgs_id: 'PGS000001',
  pgs_license: 'CC-BY',
  development_method: 'C+T',
  ancestry_mismatch: false,
  ancestry_warning_text: ANCESTRY_NOT_RUN,
  evidence_level: 2,
}

const TRAITS_PRS = {
  trait: 'height',
  name: 'Height',
  percentile: 60,
  z_score: 0.25,
  bootstrap_ci_lower: 50,
  bootstrap_ci_upper: 70,
  source_ancestry: 'EUR',
  source_study: 'PGS000001',
  snps_used: 900,
  snps_total: 1000,
  coverage_fraction: 0.9,
  ancestry_mismatch: false,
  ancestry_warning_text: ANCESTRY_NOT_RUN,
  is_sufficient: true,
  calibrated: true,
  research_use_only: true,
  evidence_level: 2,
  pgs_id: 'PGS000001',
  pgs_license: 'CC0',
  development_method: 'C+T',
  genome_build: 'GRCh37',
  variants_number: 1000,
  source_url: 'https://www.pgscatalog.org/score/PGS000001/',
  monogenic_genes: [],
  monogenic_carrier_genes: [],
  monogenic_note: null,
}

test.describe('PRS ancestry caveats render when ancestry inference has not run (#940)', () => {
  test('shared PRS gauge shows backend warning text even without mismatch', async ({ page }) => {
    await page.route('**/api/analysis/ebmd/run**', async (route) => {
      await route.fulfill(jsonRoute({ status: 'complete' }))
    })
    await page.route('**/api/analysis/ebmd/prs**', async (route) => {
      await route.fulfill(
        jsonRoute({
          available: true,
          recommended_pgs_id: 'PGS000001',
          prs: SHARED_PRS,
          context: {},
          research_use_only: true,
        }),
      )
    })

    await page.goto('/ebmd?sample_id=1')
    await waitForReactHydration(page)

    const card = page.getByTestId('ebmd-prs').getByTestId('cancer-prs-card')
    await expect(card).toBeVisible()
    await expect(card).toHaveClass(/border-amber-300/)

    const warning = card.getByTestId('ancestry-mismatch-warning')
    await expect(warning).toBeVisible()
    await expect(warning).toContainText('Ancestry inference has not been run')
  })

  test('traits PRS gauge shows backend warning text even without mismatch', async ({ page }) => {
    await page.route('**/api/analysis/traits/prs**', async (route) => {
      await route.fulfill(jsonRoute({ items: [TRAITS_PRS], total: 1, module_disclaimer: '' }))
    })
    await page.route('**/api/analysis/traits/pathways**', async (route) => {
      await route.fulfill(
        jsonRoute({ items: [], total: 0, cross_module: [], module_disclaimer: null }),
      )
    })

    await page.goto('/traits?sample_id=1')
    await waitForReactHydration(page)

    const card = page.getByTestId('traits-prs-card')
    await expect(card).toBeVisible()
    await expect(card).toHaveClass(/border-amber-300/)
    await expect(card.getByText(/95% CI/)).toHaveCount(0)

    const warning = card.getByTestId('ancestry-mismatch-warning')
    await expect(warning).toBeVisible()
    await expect(warning).toContainText('Ancestry inference has not been run')
  })
})
