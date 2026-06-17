/**
 * Issue #979 - traits pathway detail must not label on-array no-calls as
 * "not on array". A no-call may be recoverable by re-testing; an off-chip SNP
 * is an array coverage gap.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

function jsonRoute(payload: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(payload) }
}

const OFF_CHIP_RSID = 'rs2164273'
const NO_CALL_RSID = 'rs1396862'

const SUMMARY = {
  pathway_id: 'personality_big_five',
  pathway_name: 'Personality Dimensions (Big Five)',
  level: 'Standard',
  evidence_level: 1,
  prs_primary: false,
  called_snps: 0,
  total_snps: 5,
  missing_snps: [OFF_CHIP_RSID, NO_CALL_RSID],
  no_call_snps: [NO_CALL_RSID],
  pmids: [],
}

const PATHWAYS = {
  items: [SUMMARY],
  total: 1,
  cross_module: [],
  module_disclaimer: 'Research use only.',
}

const DETAIL = {
  ...SUMMARY,
  snp_details: [],
}

test.describe('Traits no-call pathway labels (#979)', () => {
  test('pathway detail separates on-array no-calls from off-chip SNPs', async ({ page }) => {
    await page.route('**/api/analysis/traits/disclaimer', async (route) => {
      await route.fulfill(
        jsonRoute({
          disclaimer: 'Research use only.',
          evidence_cap: 2,
          research_use_only: true,
        }),
      )
    })
    await page.route('**/api/analysis/traits/prs**', async (route) => {
      await route.fulfill(jsonRoute({ items: [], total: 0, module_disclaimer: '' }))
    })
    await page.route('**/api/analysis/traits/pathways**', async (route) => {
      await route.fulfill(jsonRoute(PATHWAYS))
    })
    await page.route('**/api/analysis/traits/pathway/personality_big_five**', async (route) => {
      await route.fulfill(jsonRoute(DETAIL))
    })

    await page.goto('/traits?sample_id=1')
    await waitForReactHydration(page)
    await page.getByRole('button', { name: /Personality Dimensions/ }).first().click()

    const panel = page.getByRole('dialog', {
      name: /Personality Dimensions \(Big Five\) pathway details/,
    })
    await expect(panel).toBeVisible()
    await expect(panel).toContainText('1 not on array')
    await expect(panel).toContainText('1 no-call')

    const offChipLine = panel.getByText(/^Not on array:/)
    await expect(offChipLine).toContainText(OFF_CHIP_RSID)
    await expect(offChipLine).not.toContainText(NO_CALL_RSID)

    const noCallLine = panel.getByText(/^No call \(on the array/)
    await expect(noCallLine).toContainText(NO_CALL_RSID)
    await expect(noCallLine).not.toContainText(OFF_CHIP_RSID)
  })
})
