/**
 * Issue #589 — the shared PRSGaugeCard footer was rendered unconditionally as
 * `Source: {study} ({ancestry}, n={sample_size})`. The FH (/fh) and eBMD (/ebmd)
 * views adapt their PRS to the gauge via `toGaugePrs` with `source_ancestry=""` and
 * `sample_size=0` (those APIs carry neither field), so the footer rendered the
 * broken, misleading `Source: … (, n=0)` — a stray comma plus a false "0 samples".
 *
 * This drives the real eBMD view (run mutation + prs query stubbed; the view reads
 * `sample_id` from the URL) and asserts the footer now omits the empty parenthetical.
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

// EbmdPrs carries NO source_ancestry / sample_size — the view fills them with the
// placeholder ("" / 0) that triggered the bug.
const EBMD_PRS = {
  name: 'Heel eBMD',
  calibrated: true,
  percentile: 55,
  snps_used: 1100,
  snps_total: 1200,
  coverage_fraction: 0.92,
  is_sufficient: true,
  source_study: 'Graham et al. 2021',
  source_pmid: '33462484',
  pgs_id: 'PGS000001',
  pgs_license: 'CC-BY',
  development_method: 'C+T',
  ancestry_mismatch: false,
  ancestry_warning_text: null,
  evidence_level: 2,
}

const EBMD_RESPONSE = {
  available: true,
  recommended_pgs_id: 'PGS000001',
  prs: EBMD_PRS,
  context: {},
  research_use_only: true,
}

test.describe('PRS gauge source footer drops the empty "(, n=0)" placeholder (#589)', () => {
  test('eBMD gauge footer shows the study with no stray comma or false n=0', async ({ page }) => {
    // The view fires the run mutation on mount; an unstubbed POST flips the page
    // to a full-page error, so stub both the run and the prs query.
    await page.route('**/api/analysis/ebmd/run**', async (route) => {
      await route.fulfill(jsonRoute({ status: 'complete' }))
    })
    await page.route('**/api/analysis/ebmd/prs**', async (route) => {
      await route.fulfill(jsonRoute(EBMD_RESPONSE))
    })

    await page.goto('/ebmd?sample_id=1')
    await waitForReactHydration(page)

    const card = page.getByTestId('ebmd-prs').getByTestId('cancer-prs-card')
    await expect(card).toBeVisible()

    // The footer names the source study...
    await expect(card.getByText(/Source:\s*Graham et al\. 2021/)).toBeVisible()
    // ...with NO broken "(, n=0)" parenthetical (the bug): no stray comma, no n=0.
    await expect(card.getByText(/\(,/)).toHaveCount(0)
    await expect(card.getByText(/n=0/)).toHaveCount(0)
  })
})
