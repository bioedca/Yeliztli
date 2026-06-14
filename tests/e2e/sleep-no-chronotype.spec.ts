/**
 * Issue #615 — the sleep "Chronotype & Circadian Rhythm" pathway was permanently
 * dead: its sole marker rs57875989 IS the PER3 54-bp VNTR (deprecated/unplaced,
 * not array-typeable) and no validated tag SNP exists, so it could never fire. It
 * was removed from the panel and the now-orphaned ChronotypeDial deleted from the
 * Sleep view.
 *
 * This renders the Sleep view (route-stubbed) and asserts the three remaining
 * pathways show and the chronotype dial/section is gone.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const SLEEP_PATHWAYS = {
  items: [
    {
      pathway_id: 'caffeine_sleep',
      pathway_name: 'Caffeine & Sleep',
      level: 'Elevated',
      evidence_level: 3,
      called_snps: 2,
      total_snps: 2,
      missing_snps: [],
      pmids: [],
    },
    {
      pathway_id: 'sleep_quality',
      pathway_name: 'Sleep Quality',
      level: 'Standard',
      evidence_level: 1,
      called_snps: 1,
      total_snps: 2,
      missing_snps: [],
      pmids: [],
    },
    {
      pathway_id: 'sleep_disorders',
      pathway_name: 'Sleep Disorders',
      level: 'Elevated',
      evidence_level: 2,
      called_snps: 2,
      total_snps: 2,
      missing_snps: [],
      pmids: [],
    },
  ],
  cross_module: [],
  metabolizer: null,
}

test.describe('Sleep view has no dead chronotype/PER3 dial (#615)', () => {
  test('renders the three remaining pathways and no chronotype dial', async ({ page }) => {
    await page.route('**/api/analysis/sleep/pathways**', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(SLEEP_PATHWAYS),
      }),
    )

    await page.goto('/sleep?sample_id=1')
    await waitForReactHydration(page)

    // The three remaining pathways render (as PathwayCard headings). Sleep
    // Disorders also appears in the disorder-risk card (Elevated), so use first().
    await expect(page.getByRole('heading', { name: 'Caffeine & Sleep' }).first()).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Sleep Quality' }).first()).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Sleep Disorders' }).first()).toBeVisible()

    // The dead PER3 chronotype pathway and its dial are gone (#615).
    await expect(page.getByText('Chronotype Tendency')).toHaveCount(0)
    await expect(page.getByText('Chronotype & Circadian Rhythm')).toHaveCount(0)
  })
})
