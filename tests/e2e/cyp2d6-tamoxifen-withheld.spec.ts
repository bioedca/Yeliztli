/** Issue #2019 — a clinical-evidence hold must never be rendered as an
 * uncallable CYP2D6 result or leak the retained audit-only source wording. */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

function jsonRoute(payload: unknown) {
  return { status: 200, contentType: 'application/json', body: JSON.stringify(payload) }
}

const EMPTY_GENES = { items: [], total: 0 }
const TAMOXIFEN = {
  items: [
    {
      drug: 'tamoxifen',
      genes: ['CYP2D6'],
      classification: null,
      prescribing_guidance_withheld: true,
    },
  ],
  total: 1,
}
const EMPTY_REPORT = {
  reference_bias_disclosure: '',
  genes_assessed: 0,
  drugs_assessed: 0,
  actionable_drug_count: 0,
  gene_coverage: [],
  drugs: [],
}
const WITHHELD_DETAIL = {
  drug: 'tamoxifen',
  gene_effects: [
    {
      gene: 'CYP2D6',
      diplotype: null,
      metabolizer_status: null,
      recommendation: 'Audit-only source wording that must not render.',
      classification: 'A',
      guideline_url: 'https://example.test/audit-only-source',
      call_confidence: null,
      confidence_note: null,
      evidence_level: null,
      activity_score: null,
      ehr_notation: null,
      involved_rsids: [],
      gene_caveat: null,
      recommendation_status: 'withheld',
      not_assessed: false,
    },
  ],
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await page.route('**/api/analysis/pharma/genes**', (route) =>
    route.fulfill(jsonRoute(EMPTY_GENES)),
  )
  await page.route('**/api/analysis/pharma/drugs', (route) => route.fulfill(jsonRoute(TAMOXIFEN)))
  await page.route('**/api/analysis/pharma/report**', (route) =>
    route.fulfill(jsonRoute(EMPTY_REPORT)),
  )
  await page.route('**/api/analysis/pharma/drug/tamoxifen**', (route) =>
    route.fulfill(jsonRoute(WITHHELD_DETAIL)),
  )
})

test('renders a clinical recommendation hold without an uncallable or source-advice leak', async ({
  page,
}) => {
  await page.goto('/pharmacogenomics?sample_id=1')
  await waitForReactHydration(page)

  await expect(page.getByText('Guidance withheld', { exact: true })).toBeVisible()
  await expect(page.getByTitle('Clinical recommendation withheld')).toBeVisible()
  await page.getByText('tamoxifen', { exact: true }).click()

  const dialog = page.getByRole('dialog', { name: 'tamoxifen drug detail' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('Clinical recommendation withheld', { exact: true })).toBeVisible()
  await expect(dialog.getByText(/independent clinical validation is unresolved/i)).toBeVisible()
  await expect(dialog.getByText('CYP2D6', { exact: true })).toBeVisible()
  await expect(dialog.getByText('Not assessed', { exact: true })).toHaveCount(0)
  await expect(dialog.getByText('Audit-only source wording that must not render.')).toHaveCount(0)
  await expect(dialog.getByText('CPIC Level A')).toHaveCount(0)
  await expect(dialog.getByText('CPIC Guideline')).toHaveCount(0)
})
