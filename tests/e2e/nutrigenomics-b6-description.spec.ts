/**
 * Issue #722 — every nutrigenomics PathwayCard must render a pathway blurb.
 *
 * Vitamin B6 was added to the backend panel after the frontend's hardcoded
 * description map, so the card rendered without the educational text that its
 * sibling nutrient cards show.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

function jsonRoute(payload: unknown) {
  return { status: 200, contentType: 'application/json', body: JSON.stringify(payload) }
}

test('Vitamin B6 PathwayCard renders its description blurb', async ({ page }) => {
  await page.route('**/api/analysis/nutrigenomics/pathways**', async (route) => {
    await route.fulfill(
      jsonRoute({
        items: [
          {
            pathway_id: 'vitamin_b6',
            pathway_name: 'Vitamin B6',
            level: 'Moderate',
            evidence_level: 2,
            called_snps: 1,
            total_snps: 1,
            missing_snps: [],
            pmids: ['19744961'],
          },
        ],
        total: 1,
      }),
    )
  })

  await page.goto('/nutrigenomics?sample_id=1')
  await waitForReactHydration(page)

  await expect(page.getByRole('heading', { name: 'Vitamin B6' })).toBeVisible()
  await expect(
    page.getByText(
      "Variants affecting vitamin B6 (pyridoxal 5'-phosphate) catabolism and circulating levels.",
    ),
  ).toBeVisible()
})
