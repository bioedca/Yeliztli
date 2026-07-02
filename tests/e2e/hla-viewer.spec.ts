/**
 * Wave D SW-D5 — the HLA (imputed) page shows a raw imputed-HLA allele table with
 * the load-bearing never-for-transplant guard and a CSV export. The guard must be
 * prominent; imputed HLA is never valid for donor matching.
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

const drugUnavailable = {
  available: false,
  any_at_risk: false,
  assessments: [],
  caveat: 'imputed — confirm with clinical HLA typing.',
  unavailable_note: 'No imputed HLA calls.',
  research_use_only: true,
}

test.describe('HLA raw viewer (SW-D5)', () => {
  test('renders the allele table with the never-for-transplant guard', async ({ page }) => {
    await page.route('**/api/hla/drug-hypersensitivity**', (route) =>
      route.fulfill(jsonRoute(drugUnavailable)),
    )
    await page.route('**/api/hla/alleles**', (route) =>
      route.fulfill(
        jsonRoute({
          available: true,
          caveat: 'imputed — confirm with clinical HLA typing.',
          transplant_guard:
            'These HLA types are statistically imputed and must NEVER be used for transplant, organ, or stem-cell donor/recipient matching.',
          unavailable_note: null,
          research_use_only: true,
          alleles: [
            {
              locus: 'A',
              allele1: '01:01',
              allele2: '02:01',
              prob: 0.98,
              low_confidence: false,
              source: 'hibag',
              ancestry_model: 'European',
            },
            {
              locus: 'B',
              allele1: '57:01',
              allele2: '07:02',
              prob: 0.4,
              low_confidence: true,
              source: 'hibag',
              ancestry_model: 'European',
            },
          ],
        }),
      ),
    )

    await page.goto('/hla?sample_id=1')
    await waitForReactHydration(page)

    await expect(page.getByTestId('hla-viewer')).toBeVisible()
    await expect(page.getByTestId('hla-transplant-guard')).toContainText(
      'must NEVER be used for transplant',
    )
    await expect(page.getByTestId('hla-allele-A')).toContainText('A*01:01 / A*02:01')
    await expect(page.getByTestId('hla-allele-B')).toContainText('low')
    await expect(page.getByTestId('hla-viewer-download')).toBeVisible()
  })

  test('omits the viewer when no HLA calls exist', async ({ page }) => {
    await page.route('**/api/hla/drug-hypersensitivity**', (route) =>
      route.fulfill(jsonRoute(drugUnavailable)),
    )
    await page.route('**/api/hla/alleles**', (route) =>
      route.fulfill(
        jsonRoute({
          available: false,
          alleles: [],
          caveat: 'c',
          transplant_guard: 'g',
          unavailable_note: 'No imputed HLA calls.',
          research_use_only: true,
        }),
      ),
    )

    await page.goto('/hla?sample_id=1')
    await waitForReactHydration(page)

    await expect(page.getByTestId('hla-viewer')).toHaveCount(0)
  })
})
