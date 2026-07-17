/**
 * Issue #1994 — All Findings evidence-tier headings must show true server
 * totals, not the size of the currently loaded 200/400-row window.
 */

import { expect, test } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const INITIAL_LIMIT = 200
const TOTAL_FINDINGS = 1_000

const jsonRoute = (payload: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

function finding(index: number) {
  return {
    id: index,
    module: 'rare_variants',
    category: 'rare',
    evidence_level: 1,
    gene_symbol: `GENE${index % 20}`,
    rsid: `rs_tier_${index}`,
    finding_text: `Preliminary finding ${index}`,
    phenotype: null,
    conditions: null,
    zygosity: 'het',
    clinvar_significance: null,
    diplotype: null,
    metabolizer_status: null,
    drug: null,
    haplogroup: null,
    prs_score: null,
    prs_percentile: null,
    pathway: null,
    pathway_level: null,
    svg_path: null,
    pmid_citations: [],
    detail: null,
    created_at: '2026-07-16T00:00:00',
  }
}

const SUMMARY = {
  total_findings: TOTAL_FINDINGS,
  modules: [
    {
      module: 'rare_variants',
      count: TOTAL_FINDINGS,
      max_evidence_level: 1,
      top_finding_text: 'Preliminary finding 1',
      evidence_level_counts: [{ evidence_level: 1, count: TOTAL_FINDINGS }],
    },
  ],
  evidence_level_counts: [{ evidence_level: 1, count: TOTAL_FINDINGS }],
  high_confidence_findings: [],
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

test('tier heading keeps the true total after Load more (#1994)', async ({ page }) => {
  const requestedLimits: number[] = []

  await page.route('**/api/analysis/findings**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/summary')) {
      await route.fulfill(jsonRoute(SUMMARY))
      return
    }

    const limit = Number(url.searchParams.get('limit') ?? INITIAL_LIMIT)
    requestedLimits.push(limit)
    const findings = Array.from(
      { length: Math.min(limit, TOTAL_FINDINGS) },
      (_, i) => finding(i + 1),
    )
    await route.fulfill(jsonRoute(findings))
  })

  await page.goto('/findings?sample_id=1')
  await waitForReactHydration(page)

  const preliminaryTier = () => page.getByRole('region', { name: 'Preliminary Evidence' })
  await expect(preliminaryTier().getByText('(1000)', { exact: true })).toBeVisible()
  await expect(page.getByText('Showing the top 200 findings of 1000')).toBeVisible()

  await page.getByRole('button', { name: 'Load more findings' }).click()

  await expect.poll(() => requestedLimits).toEqual([INITIAL_LIMIT, INITIAL_LIMIT * 2])
  await expect(page.getByText('Preliminary finding 400')).toBeVisible()
  await expect(preliminaryTier().getByText('(1000)', { exact: true })).toBeVisible()
  await expect(preliminaryTier().getByText('(400)', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Showing the top 400 findings of 1000')).toBeVisible()
})
