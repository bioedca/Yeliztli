/**
 * Issue #1091 - categorical pathway cards must not present incomplete Standard
 * results as whole-panel negatives when tracked SNPs are off-chip.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

function jsonRoute(payload: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(payload) }
}

const AOC1_OFF_CHIP = ['rs10156191', 'rs1049742', 'rs1049793', 'rs2052129']

const HISTAMINE_SUMMARY = {
  pathway_id: 'histamine_metabolism',
  pathway_name: 'Histamine Metabolism',
  level: 'Standard',
  evidence_level: 1,
  called_snps: 1,
  total_snps: 5,
  missing_snps: AOC1_OFF_CHIP,
  no_call_snps: [],
  pmids: [],
  hla_proxy_lookup: null,
}

const PATHWAYS = {
  items: [HISTAMINE_SUMMARY],
  total: 1,
  cross_module: [],
  celiac_combined: null,
  histamine_combined: null,
}

test.describe('Categorical incomplete Standard coverage (#1091)', () => {
  test('allergy pathway card qualifies Standard as tested-SNP-only when AOC1 SNPs are off-chip', async ({
    page,
  }) => {
    await page.route('**/api/analysis/allergy/pathways**', async (route) => {
      await route.fulfill(jsonRoute(PATHWAYS))
    })

    await page.goto('/allergy?sample_id=1')
    await waitForReactHydration(page)

    const card = page.getByRole('button', {
      name: /Histamine Metabolism — Tested Standard/,
    })
    await expect(card).toBeVisible()
    await expect(card).toContainText('Tested Standard')
    await expect(card).toContainText(
      'No variants of concern among tested SNPs; 4 tracked SNPs (4 off-chip) not assessed.',
    )
    await expect(card).toContainText('1/5 SNPs called')
    await expect(card).not.toContainText('Standard (no variants of concern)')
  })
})
