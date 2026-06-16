/**
 * Issue #979 — fitness pathway detail must not label on-array no-calls as
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

const OFF_CHIP_RSID = 'rs8192678'
const NO_CALL_RSID = 'rs1815739'

const PATHWAYS = {
  total: 1,
  cross_context: [],
  items: [
    {
      pathway_id: 'endurance',
      pathway_name: 'Endurance',
      level: 'Standard',
      evidence_level: 2,
      called_snps: 1,
      total_snps: 3,
      missing_snps: [OFF_CHIP_RSID, NO_CALL_RSID],
      no_call_snps: [NO_CALL_RSID],
      indeterminate_snps: [],
      pmids: [],
    },
  ],
}

const DETAIL = {
  pathway_id: 'endurance',
  pathway_name: 'Endurance',
  level: 'Standard',
  evidence_level: 2,
  called_snps: 1,
  total_snps: 3,
  missing_snps: [OFF_CHIP_RSID, NO_CALL_RSID],
  no_call_snps: [NO_CALL_RSID],
  indeterminate_snps: [],
  pmids: [],
  snp_details: [],
}

test.describe('Fitness no-call pathway labels (#979)', () => {
  test('pathway detail separates on-array no-calls from off-chip SNPs', async ({ page }) => {
    await page.route('**/api/analysis/fitness/pathways**', async (route) => {
      await route.fulfill(jsonRoute(PATHWAYS))
    })
    await page.route('**/api/analysis/fitness/pathway/endurance**', async (route) => {
      await route.fulfill(jsonRoute(DETAIL))
    })

    await page.goto('/fitness?sample_id=1')
    await waitForReactHydration(page)
    await page.getByRole('button', { name: /Endurance/ }).first().click()

    const panel = page.getByRole('dialog', { name: /Endurance pathway details/ })
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
