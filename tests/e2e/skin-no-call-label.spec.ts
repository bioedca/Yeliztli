/**
 * Issue #979 - skin pathway detail must not label on-array no-calls as
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

const OFF_CHIP_RSID = 'rs1805008'
const NO_CALL_RSID = 'rs1805007'

const SUMMARY = {
  pathway_id: 'pigmentation_uv',
  pathway_name: 'Pigmentation & UV Response',
  level: 'Standard',
  evidence_level: 2,
  called_snps: 1,
  total_snps: 3,
  missing_snps: [OFF_CHIP_RSID, NO_CALL_RSID],
  no_call_snps: [NO_CALL_RSID],
  pmids: [],
}

const PATHWAYS = {
  items: [SUMMARY],
  total: 1,
  mc1r_aggregate: null,
  cross_module: [],
  insufficient_data: [],
}

const DETAIL = {
  ...SUMMARY,
  snp_details: [],
}

test.describe('Skin no-call pathway labels (#979)', () => {
  test('pathway detail separates on-array no-calls from off-chip SNPs', async ({ page }) => {
    await page.route('**/api/analysis/skin/pathways**', async (route) => {
      await route.fulfill(jsonRoute(PATHWAYS))
    })
    await page.route('**/api/analysis/skin/pathway/pigmentation_uv**', async (route) => {
      await route.fulfill(jsonRoute(DETAIL))
    })

    await page.goto('/skin?sample_id=1')
    await waitForReactHydration(page)
    await page.getByRole('button', { name: /Pigmentation & UV Response/ }).first().click()

    const panel = page.getByRole('dialog', { name: /Pigmentation & UV Response pathway details/ })
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
