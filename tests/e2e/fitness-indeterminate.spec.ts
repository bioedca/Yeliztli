/**
 * Issue #360/#961 — the fitness PathwayCard must surface an indeterminate
 * caveat so an all-indeterminate "Standard" pathway no longer reads as a
 * confidently-clear "no variants of concern" result (#270/#356), without
 * asserting the wrong cause for unmodeled-allele calls (#608).
 *
 * The backend exposes `indeterminate_snps` on the pathways response; here we
 * stub that one endpoint (FitnessView reads `sample_id` from the URL) and
 * assert the caveat renders and the pathway is not presented as clear.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

function jsonRoute(payload: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(payload) }
}

const PATHWAYS_WITH_INDETERMINATE = {
  total: 1,
  cross_context: [],
  items: [
    {
      pathway_id: 'training_response',
      pathway_name: 'Training Response',
      level: 'Standard',
      evidence_level: 1,
      called_snps: 1,
      total_snps: 1,
      missing_snps: [],
      indeterminate_snps: ['rs9939609'],
      pmids: [],
    },
  ],
}

const PATHWAYS_CLEAR = {
  total: 1,
  cross_context: [],
  items: [
    {
      pathway_id: 'recovery_injury',
      pathway_name: 'Recovery & Injury',
      level: 'Standard',
      evidence_level: 1,
      called_snps: 2,
      total_snps: 2,
      missing_snps: [],
      indeterminate_snps: [],
      pmids: [],
    },
  ],
}

const PATHWAYS_WITH_UNMODELED_INDETERMINATE = {
  total: 1,
  cross_context: [],
  items: [
    {
      pathway_id: 'power',
      pathway_name: 'Power',
      level: 'Standard',
      evidence_level: 2,
      called_snps: 1,
      total_snps: 1,
      missing_snps: [],
      indeterminate_snps: ['rs4341'],
      pmids: [],
    },
  ],
}

test.describe('Fitness PathwayCard indeterminate caveat (#360/#961)', () => {
  test('an all-indeterminate Standard pathway shows a neutral caveat, not "no concern"', async ({
    page,
  }) => {
    await page.route('**/api/analysis/fitness/pathways**', async (route) => {
      await route.fulfill(jsonRoute(PATHWAYS_WITH_INDETERMINATE))
    })

    await page.goto('/fitness?sample_id=1')
    await waitForReactHydration(page)

    // The card renders with its Standard level badge...
    await expect(page.getByText('Training Response')).toBeVisible()
    await expect(page.getByText('Standard')).toBeVisible()

    // ...but it is NOT presented as confidently clear: the neutral caveat shows.
    const caveat = page.getByTestId('pathway-indeterminate-caveat')
    await expect(caveat).toBeVisible()
    await expect(caveat).toContainText(/not interpreted/i)
    await expect(caveat).toContainText(/see details/i)
    await expect(caveat).not.toContainText(/strand-unresolved/i)
  })

  test('an unmodeled-allele indeterminate pathway does not claim strand-unresolved', async ({
    page,
  }) => {
    await page.route('**/api/analysis/fitness/pathways**', async (route) => {
      await route.fulfill(jsonRoute(PATHWAYS_WITH_UNMODELED_INDETERMINATE))
    })

    await page.goto('/fitness?sample_id=1')
    await waitForReactHydration(page)

    await expect(page.getByText('Power')).toBeVisible()
    const caveat = page.getByTestId('pathway-indeterminate-caveat')
    await expect(caveat).toContainText(/1 variant observed but not interpreted/i)
    await expect(caveat).not.toContainText(/strand-unresolved/i)
  })

  test('a confidently-clear Standard pathway shows no caveat', async ({ page }) => {
    await page.route('**/api/analysis/fitness/pathways**', async (route) => {
      await route.fulfill(jsonRoute(PATHWAYS_CLEAR))
    })

    await page.goto('/fitness?sample_id=1')
    await waitForReactHydration(page)

    await expect(page.getByText('Recovery & Injury')).toBeVisible()
    await expect(page.getByTestId('pathway-indeterminate-caveat')).toHaveCount(0)
  })
})
