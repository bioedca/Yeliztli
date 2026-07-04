/**
 * Wave D SW-D2 — the HLA (imputed) page surfaces per-drug hypersensitivity risk
 * from imputed classical-HLA calls, framed with the confirmatory-typing caveat.
 * A carrier (HLA-B*57:01) must show an at-risk card with the CPIC recommendation;
 * a sample with no imputed HLA calls must show the unavailable state.
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

const CAVEAT =
  'HLA alleles here are statistically imputed from SNP genotypes, not directly typed. ' +
  'Confirm with clinical high-resolution HLA typing before acting on any result; ' +
  'never use imputed HLA for transplant or donor matching.'

test.describe('HLA drug hypersensitivity (SW-D2)', () => {
  test('surfaces an at-risk carrier card with the CPIC recommendation + caveat', async ({
    page,
  }) => {
    await page.route('**/api/hla/drug-hypersensitivity**', (route) =>
      route.fulfill(
        jsonRoute({
          available: true,
          any_at_risk: true,
          caveat: CAVEAT,
          unavailable_note: null,
          research_use_only: true,
          assessments: [
            {
              allele: 'HLA-A*31:01',
              drugs: ['carbamazepine'],
              reaction: 'carbamazepine hypersensitivity',
              status: 'no_risk_allele',
              carried: false,
              zygosity: null,
              copies: 0,
              prob: 0.9,
              low_confidence: false,
              recommendation: 'HLA-A*31:01 not detected.',
              guideline: 'CPIC',
              citations: ['PMID:29392710'],
              notes: [],
            },
            {
              allele: 'HLA-B*57:01',
              drugs: ['abacavir'],
              reaction: 'abacavir hypersensitivity reaction',
              status: 'at_risk',
              carried: true,
              zygosity: 'heterozygous',
              copies: 1,
              prob: 0.96,
              low_confidence: false,
              recommendation: 'CPIC: do not prescribe abacavir to HLA-B*57:01-positive patients.',
              guideline: 'CPIC',
              citations: ['PMID:24561393'],
              notes: [],
            },
          ],
        }),
      ),
    )

    await page.goto('/hla?sample_id=1')
    await waitForReactHydration(page)

    await expect(page.getByTestId('hla-caveat')).toContainText('never use imputed HLA for transplant')

    const section = page.getByTestId('hla-drug-hypersensitivity')
    await expect(section).toContainText('HLA-B*57:01')
    await expect(section).toContainText('abacavir')
    await expect(section).toContainText('do not prescribe abacavir')

    // The at-risk card carries its status and sorts to the top.
    const cards = page.locator('[data-testid^="hla-drug-HLA-"]')
    await expect(cards.first()).toHaveAttribute('data-status', 'at_risk')
  })

  test('shows the unavailable state when no imputed HLA calls exist', async ({ page }) => {
    await page.route('**/api/hla/drug-hypersensitivity**', (route) =>
      route.fulfill(
        jsonRoute({
          available: false,
          any_at_risk: false,
          assessments: [],
          caveat: CAVEAT,
          unavailable_note: 'No imputed HLA calls are available for this sample.',
          research_use_only: true,
        }),
      ),
    )

    await page.goto('/hla?sample_id=1')
    await waitForReactHydration(page)

    await expect(page.getByTestId('hla-drug-hypersensitivity')).toContainText(
      'No imputed HLA calls for this sample.',
    )
    await expect(
      page.getByTestId('hla-drug-hypersensitivity').getByRole('button', { name: 'HLA setup docs' }),
    ).toBeVisible()
    await expect(page.locator('[data-testid^="hla-drug-HLA-"]')).toHaveCount(0)
  })
})
