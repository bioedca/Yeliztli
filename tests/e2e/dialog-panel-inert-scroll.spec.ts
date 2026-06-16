/**
 * Issue #846 - slide-in detail panels are modal dialogs, so their background
 * must be DOM-inert and background scrolling must be locked while open.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const jsonRoute = (payload: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const CARRIER_VARIANTS = {
  total: 1,
  genes_with_findings: ['CFTR'],
  items: [
    {
      rsid: 'rs113993960',
      gene_symbol: 'CFTR',
      genotype: 'C/T',
      zygosity: 'het',
      clinvar_significance: 'Pathogenic',
      clinvar_accession: 'VCV000007105',
      clinvar_review_stars: 3,
      clinvar_conditions: 'Cystic fibrosis',
      conditions: ['Cystic Fibrosis'],
      inheritance: 'AR',
      evidence_level: 4,
      cross_links: [],
      pmids: ['20301428'],
      notes: 'Most common autosomal recessive condition in populations of European descent.',
    },
  ],
}

test.describe('slide-in detail panel modal isolation (#846)', () => {
  test('opening a carrier detail panel inert-locks the background and scroll container', async ({
    page,
  }) => {
    await page.route('**/api/analysis/carrier/variants**', async (route) => {
      await route.fulfill(jsonRoute(CARRIER_VARIANTS))
    })
    await page.route('**/api/analysis/carrier/disclaimer**', async (route) => {
      await route.fulfill(
        jsonRoute({
          title: 'About carrier status',
          text: 'Reproductive carrier screening reference text.',
          gene_notes: {},
        }),
      )
    })

    await page.goto('/carrier-status?sample_id=1')
    await waitForReactHydration(page)

    const findingsRegion = page.locator('section[aria-label="Carrier status findings"]')
    const mainContent = page.locator('#main-content')
    const card = page.getByTestId('carrier-variant-card').filter({ hasText: 'CFTR' })

    await expect(findingsRegion).not.toHaveAttribute('inert', '')
    await expect(card).toBeVisible()

    await card.click()

    const panel = page.getByTestId('carrier-detail-panel')
    await expect(panel).toBeVisible()
    await expect(findingsRegion).toHaveAttribute('inert', '')
    await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe('hidden')
    await expect
      .poll(() => mainContent.evaluate((node) => (node as HTMLElement).style.overflow))
      .toBe('hidden')

    // The backdrop is intentionally not inert; it still closes the modal.
    await page.mouse.click(20, 20)
    await expect(panel).toBeHidden()
    await expect(findingsRegion).not.toHaveAttribute('inert', '')
    await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe('')
    await expect.poll(() => mainContent.evaluate((node) => (node as HTMLElement).style.overflow)).toBe('')
  })
})
