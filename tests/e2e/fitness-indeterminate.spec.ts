/**
 * Issue #360 — the fitness PathwayCard must surface the strand-INDETERMINATE
 * caveat so an all-indeterminate "Standard" pathway (e.g. an FTO rs9939609
 * palindromic homozygote whose strand can't be resolved) no longer reads as a
 * confidently-clear "no variants of concern" result (#270/#356).
 *
 * The backend exposes `indeterminate_snps` on the pathways response; here we
 * stub that one endpoint (FitnessView reads `sample_id` from the URL) and
 * assert the caveat renders and the pathway is not presented as clear.
 */

import { test, expect } from '@playwright/test'
import { waitForReactHydration } from './helpers'

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

test.describe('Fitness PathwayCard strand-indeterminate caveat (#360)', () => {
  test('an all-indeterminate Standard pathway shows the caveat, not "no concern"', async ({
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
    await expect(caveat).toContainText(/strand-unresolved/i)
    await expect(caveat).toContainText(/not interpreted/i)
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
