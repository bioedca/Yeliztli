/**
 * Wave D SW-D3 — the HLA (imputed) page surfaces high-NPV disease rule-outs from
 * imputed HLA-DQ calls. A celiac-permissive DQ2.5 genotype is shown as
 * non-diagnostic; DQB1*06:02-negative is shown as arguing strongly against
 * narcolepsy type 1 (a rule-out, framed as "not a full exclusion"). Low-confidence
 * imputed calls render as indeterminate rather than reassuring negatives.
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

test.describe('HLA disease rule-outs (SW-D3)', () => {
  test('shows celiac (permissive, non-diagnostic) + narcolepsy (absent, argues against)', async ({
    page,
  }) => {
    // Drug section unavailable — keeps the page clean for the rule-outs assertion.
    await page.route('**/api/hla/drug-hypersensitivity**', (route) =>
      route.fulfill(
        jsonRoute({
          available: false,
          any_at_risk: false,
          assessments: [],
          caveat: 'imputed — confirm with clinical HLA typing.',
          unavailable_note: 'No imputed HLA calls.',
          research_use_only: true,
        }),
      ),
    )
    await page.route('**/api/hla/rule-outs**', (route) =>
      route.fulfill(
        jsonRoute({
          available: true,
          caveat: 'imputed — confirm with clinical HLA typing.',
          unavailable_note: null,
          research_use_only: true,
          citations: ['PMID:31274511', 'PMID:30321823'],
          celiac: {
            status: 'permissive_present',
            detected: ['DQ2.5 (DQA1*05 + DQB1*02:01)'],
            low_confidence: false,
            interpretation:
              'A celiac-permissive HLA-DQ haplotype is present. This does NOT diagnose celiac disease.',
          },
          narcolepsy: {
            status: 'absent_lowers',
            carried: false,
            zygosity: null,
            low_confidence: false,
            interpretation:
              'HLA-DQB1*06:02 was not detected. Its absence argues strongly against narcolepsy type 1 but does not fully exclude it.',
          },
        }),
      ),
    )

    await page.goto('/hla?sample_id=1')
    await waitForReactHydration(page)

    const section = page.getByTestId('hla-rule-outs')
    await expect(section).toBeVisible()

    const celiac = page.getByTestId('hla-rule-out-celiac')
    await expect(celiac).toHaveAttribute('data-tone', 'non_diagnostic')
    await expect(celiac).toContainText('DQ2.5')
    await expect(celiac).toContainText('does NOT diagnose celiac')

    const narco = page.getByTestId('hla-rule-out-narcolepsy')
    await expect(narco).toHaveAttribute('data-tone', 'reassuring')
    await expect(narco).toContainText('argues strongly against narcolepsy')
  })

  test('omits the rule-outs section when no HLA calls exist', async ({ page }) => {
    await page.route('**/api/hla/drug-hypersensitivity**', (route) =>
      route.fulfill(
        jsonRoute({
          available: false,
          any_at_risk: false,
          assessments: [],
          caveat: 'c',
          unavailable_note: 'No imputed HLA calls.',
          research_use_only: true,
        }),
      ),
    )
    await page.route('**/api/hla/rule-outs**', (route) =>
      route.fulfill(
        jsonRoute({
          available: false,
          celiac: null,
          narcolepsy: null,
          caveat: 'c',
          unavailable_note: 'No imputed HLA calls.',
          citations: [],
          research_use_only: true,
        }),
      ),
    )

    await page.goto('/hla?sample_id=1')
    await waitForReactHydration(page)

    await expect(page.getByTestId('hla-rule-outs')).toHaveCount(0)
  })

  test('shows low-confidence rule-outs as indeterminate rather than reassuring', async ({
    page,
  }) => {
    await page.route('**/api/hla/drug-hypersensitivity**', (route) =>
      route.fulfill(
        jsonRoute({
          available: false,
          any_at_risk: false,
          assessments: [],
          caveat: 'imputed - confirm with clinical HLA typing.',
          unavailable_note: 'No imputed HLA calls.',
          research_use_only: true,
        }),
      ),
    )
    await page.route('**/api/hla/rule-outs**', (route) =>
      route.fulfill(
        jsonRoute({
          available: true,
          caveat: 'imputed - confirm with clinical HLA typing.',
          unavailable_note: null,
          research_use_only: true,
          citations: ['PMID:31274511', 'PMID:30321823'],
          celiac: {
            status: 'indeterminate',
            detected: [],
            low_confidence: true,
            interpretation:
              'One or more required HLA-DQA1/DQB1 calls have low imputation confidence. Do not interpret this as either a celiac HLA rule-out or confirmed celiac-permissive HLA.',
          },
          narcolepsy: {
            status: 'indeterminate',
            carried: false,
            zygosity: null,
            low_confidence: true,
            interpretation:
              'The imputed HLA-DQB1*06:02 absence has low confidence. Do not interpret this as lowering narcolepsy type 1 likelihood until confirmed with clinical HLA typing.',
          },
        }),
      ),
    )

    await page.goto('/hla?sample_id=1')
    await waitForReactHydration(page)

    const celiac = page.getByTestId('hla-rule-out-celiac')
    await expect(celiac).toHaveAttribute('data-tone', 'indeterminate')
    await expect(celiac).toContainText('Low-confidence - indeterminate')
    await expect(celiac).toContainText('do not interpret this as positive or negative')
    await expect(celiac).not.toContainText('Very unlikely')

    const narco = page.getByTestId('hla-rule-out-narcolepsy')
    await expect(narco).toHaveAttribute('data-tone', 'indeterminate')
    await expect(narco).toContainText('Low-confidence - indeterminate')
    await expect(narco).toContainText('Do not interpret this as lowering narcolepsy')
    await expect(narco).not.toContainText('absent — argues against NT1')
  })
})
