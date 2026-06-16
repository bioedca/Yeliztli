/**
 * Issue #613 — the categorical pathway-level (Elevated / Moderate / Standard)
 * badge colour was inconsistent between the module views (PathwayCards:
 * Elevated→amber, Moderate→blue) and the All Findings page (FindingRow:
 * Elevated→red, Moderate→amber). The same amber badge meant "Elevated" in a
 * module but "Moderate" in All Findings.
 *
 * The fix routes every surface through one shared map (`@/lib/pathwayLevel`,
 * amber/blue/emerald — matching the per-SNP `snpCategory.ts`). This spec renders
 * the All Findings page (the surface that changed) with route-stubbed findings of
 * each level and asserts the badges now use the shared scale — Elevated is amber
 * (not the old red) and Moderate is blue (not the old amber).
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

function finding(
  id: number,
  finding_text: string,
  pathway_level: 'Elevated' | 'Moderate' | 'Standard',
  pathway: string,
) {
  return {
    id,
    module: 'fitness',
    category: 'test',
    evidence_level: 3,
    gene_symbol: null,
    rsid: null,
    finding_text,
    phenotype: null,
    conditions: null,
    zygosity: null,
    clinvar_significance: null,
    diplotype: null,
    metabolizer_status: null,
    drug: null,
    haplogroup: null,
    prs_score: null,
    prs_percentile: null,
    pathway,
    pathway_level,
    svg_path: null,
    pmid_citations: [],
    detail: null,
    created_at: '2026-03-17T12:00:00',
  }
}

const FINDINGS = [
  finding(1, 'Elevated pathway finding', 'Elevated', 'endurance'),
  finding(2, 'Moderate pathway finding', 'Moderate', 'power'),
  finding(3, 'Standard pathway finding', 'Standard', 'recovery'),
]

const SUMMARY = {
  total_findings: FINDINGS.length,
  modules: [],
  high_confidence_findings: [],
}

test.describe('Pathway-level badge colours are consistent across views (#613)', () => {
  test('All Findings uses the shared amber/blue/emerald scale, not the old red/amber', async ({
    page,
  }) => {
    await page.route('**/api/analysis/findings**', async (route) => {
      const url = route.request().url()
      const body = url.includes('/findings/summary') ? SUMMARY : FINDINGS
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })
    })

    await page.goto('/findings?sample_id=1')
    await waitForReactHydration(page)
    await expect(page.getByText('Elevated pathway finding')).toBeVisible()

    const badge = (level: string) =>
      page.getByTestId('finding-pathway-level').filter({ hasText: new RegExp(`^${level}$`) })

    // Elevated → amber (matches the module PathwayCards + snpCategory.ts), and is
    // NOT the old All-Findings red.
    await expect(badge('Elevated')).toHaveClass(/bg-amber-100/)
    await expect(badge('Elevated')).not.toHaveClass(/bg-red/)

    // Moderate → blue, and is NOT the old All-Findings amber (the exact overload
    // #613 reported: amber meaning "Moderate" here but "Elevated" in modules).
    await expect(badge('Moderate')).toHaveClass(/bg-blue-100/)
    await expect(badge('Moderate')).not.toHaveClass(/bg-amber/)

    // Standard → emerald.
    await expect(badge('Standard')).toHaveClass(/bg-emerald-100/)
  })
})
