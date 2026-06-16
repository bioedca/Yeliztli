/**
 * Issue #1017 — MetabolizerCard card tint must communicate phenotype/result,
 * not call-confidence. A Complete-confidence Poor Metabolizer should not render
 * with the same green card background as a Complete-confidence Normal call.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

function jsonRoute(payload: unknown) {
  return { status: 200, contentType: 'application/json', body: JSON.stringify(payload) }
}

const GENES = {
  items: [
    {
      gene: 'CYP2C19',
      diplotype: '*2/*2',
      phenotype: 'Poor Metabolizer',
      call_confidence: 'Complete',
      confidence_note: null,
      activity_score: 0,
      ehr_notation: 'CYP2C19 *2/*2',
      evidence_level: 4,
      involved_rsids: ['rs4244285'],
      drugs: ['clopidogrel', 'voriconazole'],
      gene_caveat: null,
    },
    {
      gene: 'CYP2C9',
      diplotype: '*1/*1',
      phenotype: 'Normal Metabolizer',
      call_confidence: 'Complete',
      confidence_note: null,
      activity_score: 2,
      ehr_notation: 'CYP2C9 *1/*1',
      evidence_level: 4,
      involved_rsids: ['rs1799853'],
      drugs: ['warfarin'],
      gene_caveat: null,
    },
  ],
}

const DRUGS = {
  items: [
    { drug: 'clopidogrel', genes: ['CYP2C19'], classification: 'A' },
    { drug: 'warfarin', genes: ['CYP2C9'], classification: 'A' },
  ],
}

const EMPTY_REPORT = {
  reference_bias_disclosure: '',
  genes_assessed: 0,
  drugs_assessed: 0,
  actionable_drug_count: 0,
  gene_coverage: [],
  drugs: [],
}

test('Complete Poor Metabolizer and Complete Normal cards have distinct result tints', async ({
  page,
}) => {
  await page.route('**/api/analysis/pharma/genes**', (route) => route.fulfill(jsonRoute(GENES)))
  await page.route('**/api/analysis/pharma/drugs', (route) => route.fulfill(jsonRoute(DRUGS)))
  await page.route('**/api/analysis/pharma/report**', (route) =>
    route.fulfill(jsonRoute(EMPTY_REPORT)),
  )

  await page.goto('/pharmacogenomics?sample_id=1')
  await waitForReactHydration(page)

  const poorCard = page.getByRole('article', { name: 'CYP2C19 metabolizer status' })
  const normalCard = page.getByRole('article', { name: 'CYP2C9 metabolizer status' })

  await expect(poorCard).toContainText('Poor Metabolizer')
  await expect(normalCard).toContainText('Normal Metabolizer')
  await expect(poorCard.getByText('Complete')).toHaveClass(/text-emerald-700/)
  await expect(normalCard.getByText('Complete')).toHaveClass(/text-emerald-700/)

  await expect(poorCard).toHaveClass(/bg-amber-50\/70/)
  await expect(normalCard).toHaveClass(/bg-emerald-50\/60/)
  await expect(poorCard).not.toHaveClass(/bg-emerald-50\/60/)

  const poorBackground = await poorCard.evaluate((node) => getComputedStyle(node).backgroundColor)
  const normalBackground = await normalCard.evaluate((node) => getComputedStyle(node).backgroundColor)
  expect(poorBackground).not.toBe(normalBackground)
})
