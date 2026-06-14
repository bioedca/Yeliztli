/**
 * Issue #574 — the PRS trait-architecture explainer (TraitArchitectureCard) hardcodes
 * a copy of the backend `PRS_TRAIT_ARCHITECTURE` block, and the Ding-2023 citation had
 * drifted (the frontend dropped the DOI) with no test pinning it. The backend↔frontend
 * parity is now guarded by `tests/backend/test_trait_architecture_parity.py`; this spec
 * verifies the reader actually sees the corrected, full citation in a real browser.
 *
 * The card renders inside the Traits view's PRS section, which gates on a non-empty PRS
 * list; the view also `isError`-OR's the pathways query, so both are stubbed (the view
 * reads `sample_id` from the URL).
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

const PRS_ITEM = {
  trait: 'height',
  name: 'Height',
  percentile: 60,
  z_score: 0.25,
  bootstrap_ci_lower: 50,
  bootstrap_ci_upper: 70,
  source_ancestry: 'EUR',
  source_study: 'PGS000001',
  snps_used: 900,
  snps_total: 1000,
  coverage_fraction: 0.9,
  ancestry_mismatch: false,
  ancestry_warning_text: null,
  is_sufficient: true,
  calibrated: true,
  research_use_only: true,
  evidence_level: 2,
  pgs_id: 'PGS000001',
  pgs_license: 'CC0',
  development_method: 'C+T',
  genome_build: 'GRCh37',
  variants_number: 1000,
  source_url: 'https://www.pgscatalog.org/score/PGS000001/',
  monogenic_genes: [],
  monogenic_carrier_genes: [],
  monogenic_note: null,
}

test.describe('PRS trait-architecture card shows the full canonical Ding 2023 citation (#574)', () => {
  test('the explainer embeds the page range + DOI, not a drifted short citation', async ({
    page,
  }) => {
    await page.route('**/api/analysis/traits/prs**', async (route) => {
      await route.fulfill(jsonRoute({ items: [PRS_ITEM], total: 1, module_disclaimer: '' }))
    })
    await page.route('**/api/analysis/traits/pathways**', async (route) => {
      await route.fulfill(
        jsonRoute({ items: [], total: 0, cross_module: [], module_disclaimer: null }),
      )
    })

    await page.goto('/traits?sample_id=1')
    await waitForReactHydration(page)

    // The card is a collapsible <details>; expand it via its summary.
    const card = page.getByTestId('trait-architecture-card')
    await expect(card).toBeVisible()
    await card.getByText('How to read a polygenic score').click()

    // The corrected citation carries the volume:page range AND the DOI (the part
    // that had drifted away on the frontend), matching the canonical backend block.
    await expect(
      card.getByText(/Ding et al\., Nature 618:774-781 \(2023\); doi:10\.1038\/s41586-023-06079-4/),
    ).toBeVisible()
    // The specific cross-ancestry statistic is shown alongside its source.
    await expect(card.getByText(/Pearson r ≈ −0\.95 across 84\s+traits/)).toBeVisible()
  })
})
