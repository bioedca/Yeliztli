/** Issue #2034 — APOE empty findings must explain an uncallable genotype. */

import { test, expect } from '@playwright/test'
import { bypassSetup, mockFreshSampleState, waitForReactHydration } from './helpers'

const SAMPLE_ID = 1

const jsonRoute = (body: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

test('keeps missing-SNP findings copy consistent after disclosure', async ({ page }) => {
  let acknowledged = false

  await bypassSetup(page)
  await mockFreshSampleState(page)
  await page.route('**/api/analysis/apoe/disclaimer', (route) =>
    route.fulfill(
      jsonRoute({
        title: 'APOE disclosure',
        text: 'Review this sensitive result only when you are ready.',
        accept_label: 'Show Results',
        decline_label: 'Skip',
      }),
    ),
  )
  await page.route('**/api/analysis/apoe/gate-status**', (route) =>
    route.fulfill(
      jsonRoute({
        acknowledged,
        acknowledged_at: acknowledged ? '2026-08-13T00:00:00Z' : null,
      }),
    ),
  )
  await page.route('**/api/analysis/apoe/acknowledge-gate**', (route) => {
    acknowledged = true
    return route.fulfill(
      jsonRoute({ acknowledged: true, acknowledged_at: '2026-08-13T00:00:00Z' }),
    )
  })
  await page.route('**/api/analysis/apoe/genotype**', (route) =>
    route.fulfill(
      jsonRoute({
        status: 'missing_snps',
        diplotype: null,
        has_e4: null,
        e4_count: null,
        has_e2: null,
        e2_count: null,
        rs429358_genotype: null,
        rs7412_genotype: null,
      }),
    ),
  )
  await page.route('**/api/analysis/apoe/findings**', (route) =>
    route.fulfill(jsonRoute({ items: [], total: 0 })),
  )

  await page.goto(`/apoe?sample_id=${SAMPLE_ID}`)
  await waitForReactHydration(page)

  await expect(page.getByRole('region', { name: 'APOE disclosure gate' })).toBeVisible()
  await expect(page.getByTestId('apoe-genotype-card')).toHaveCount(0)
  await page.getByTestId('apoe-gate-accept').click()

  await expect(page.getByTestId('apoe-genotype-status')).toHaveText(
    'One or both APOE SNPs (rs429358, rs7412) are missing from this sample.',
  )
  const findings = page.getByRole('region', { name: 'APOE findings' })
  const emptyState = findings.getByRole('region', { name: 'No APOE findings available.' })
  await expect(emptyState).toBeVisible()
  await expect(emptyState).toContainText(
    'One or both APOE SNPs (rs429358, rs7412) are missing from this sample.',
  )
  await expect(emptyState).not.toContainText('Run the APOE analysis first.')
})
