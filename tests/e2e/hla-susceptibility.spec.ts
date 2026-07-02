/**
 * Wave D SW-D4 — the HLA (imputed) page surfaces autoimmune-susceptibility
 * associations, framed as susceptibility markers (not diagnostic). A B*27 risk
 * subtype shows increased susceptibility; a disease-neutral B*27:06/*27:09 subtype
 * is not flagged as increased.
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

test.describe('HLA autoimmune susceptibility (SW-D4)', () => {
  test('shows an increased-susceptibility B*27 card framed as not diagnostic', async ({ page }) => {
    await page.route('**/api/hla/drug-hypersensitivity**', (route) =>
      route.fulfill(jsonRoute(drugUnavailable)),
    )
    await page.route('**/api/hla/susceptibility**', (route) =>
      route.fulfill(
        jsonRoute({
          available: true,
          caveat: 'imputed — confirm with clinical HLA typing.',
          unavailable_note: null,
          research_use_only: true,
          findings: [
            {
              condition: 'Ankylosing spondylitis / axial spondyloarthritis',
              hla: 'HLA-B*27',
              status: 'increased_risk',
              carried: true,
              detail: 'HLA-B*27:05 (heterozygous)',
              interpretation:
                'HLA-B*27 is present. This is a susceptibility marker, not a diagnosis.',
              low_confidence: false,
              citations: ['PMID:28259985'],
              notes: ['Also associates with acute anterior uveitis and reactive arthritis.'],
            },
          ],
        }),
      ),
    )

    await page.goto('/hla?sample_id=1')
    await waitForReactHydration(page)

    const section = page.getByTestId('hla-susceptibility')
    await expect(section).toBeVisible()

    const b27 = page.getByTestId('hla-susc-HLA-B*27')
    await expect(b27).toHaveAttribute('data-status', 'increased_risk')
    await expect(b27).toContainText('Ankylosing spondylitis')
    await expect(b27).toContainText('susceptibility marker, not a diagnosis')
  })

  test('omits the susceptibility section when no HLA calls exist', async ({ page }) => {
    await page.route('**/api/hla/drug-hypersensitivity**', (route) =>
      route.fulfill(jsonRoute(drugUnavailable)),
    )
    await page.route('**/api/hla/susceptibility**', (route) =>
      route.fulfill(
        jsonRoute({
          available: false,
          findings: [],
          caveat: 'c',
          unavailable_note: 'No imputed HLA calls.',
          research_use_only: true,
        }),
      ),
    )

    await page.goto('/hla?sample_id=1')
    await waitForReactHydration(page)

    await expect(page.getByTestId('hla-susceptibility')).toHaveCount(0)
  })
})
