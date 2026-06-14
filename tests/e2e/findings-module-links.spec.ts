/**
 * Issue #544 — on the All Findings page (FindingsExplorer) every finding's
 * "View module" link was built from a hardcoded MODULE_META covering only 8
 * modules. Any module missing from it fell back to route "/", so clicking a
 * Gene Health / Methylation / FH / eBMD / Fitness finding's module link dumped
 * the user on the Dashboard, and acronym labels rendered mis-cased (fh→"Fh",
 * ebmd→"Ebmd").
 *
 * The findings + summary endpoints are stubbed (the view reads sample_id from
 * the URL and gates only on the findings query) so the page renders without
 * genomic data. We assert each page-backed module links to its real route,
 * acronym labels are correctly cased, and a panel-only module (no page) renders
 * a non-navigable label rather than a link to "/".
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

function finding(id: number, module: string, finding_text: string) {
  return {
    id,
    module,
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
    pathway: null,
    pathway_level: null,
    svg_path: null,
    pmid_citations: [],
    detail: null,
    created_at: '2026-03-17T12:00:00',
  }
}

const FINDINGS = [
  finding(1, 'cancer', 'Cancer finding'), // control — already in MODULE_META
  finding(2, 'fh', 'FH finding'),
  finding(3, 'gene_health', 'Gene health finding'),
  finding(4, 'methylation', 'Methylation finding'),
  finding(5, 'ebmd', 'eBMD finding'),
  finding(6, 'fitness', 'Fitness finding'),
  finding(7, 'amd', 'AMD risk finding'), // panel-only — no dedicated page
  finding(8, 'mt_rnr1', 'MT-RNR1 finding'), // panel-only, acronym label
  finding(9, 'apol1', 'APOL1 finding'), // panel-only, acronym label
]

const SUMMARY = {
  total_findings: FINDINGS.length,
  modules: [],
  high_confidence_findings: [],
}

test.describe('All Findings module links resolve to the right page (#544)', () => {
  test('page-backed modules link to their route; acronyms cased; panel-only non-navigable', async ({
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

    await expect(page.getByText('FH finding')).toBeVisible()

    // Each page-backed module's link points at its real route (carrying sample_id),
    // never the Dashboard "/".
    const expected: Record<string, string> = {
      Cancer: '/cancer?sample_id=1',
      FH: '/fh?sample_id=1',
      'Gene Health': '/gene-health?sample_id=1',
      Methylation: '/methylation?sample_id=1',
      eBMD: '/ebmd?sample_id=1',
      Fitness: '/fitness?sample_id=1',
    }
    for (const [label, href] of Object.entries(expected)) {
      await expect(page.getByRole('link', { name: `View ${label} module` })).toHaveAttribute(
        'href',
        href,
      )
    }

    // Acronyms are correctly cased (the old fallback produced "Fh"/"Ebmd").
    await expect(page.getByText('Fh', { exact: true })).toHaveCount(0)
    await expect(page.getByText('Ebmd', { exact: true })).toHaveCount(0)

    // Panel-only modules (no page) show their correctly-cased label but are NOT
    // links — including the acronym ones that the title-case fallback would mangle.
    await expect(page.getByText('AMD', { exact: true })).toBeVisible()
    await expect(page.getByRole('link', { name: /AMD module/ })).toHaveCount(0)
    await expect(page.getByText('MT-RNR1', { exact: true })).toBeVisible()
    await expect(page.getByText('APOL1', { exact: true })).toBeVisible()
    await expect(page.getByText('Mt Rnr1', { exact: true })).toHaveCount(0)
    await expect(page.getByText('Apol1', { exact: true })).toHaveCount(0)

    // No finding-row module link silently targets the Dashboard root.
    const hrefs = await page.getByRole('link').evaluateAll((els) =>
      els.map((e) => e.getAttribute('href')),
    )
    expect(hrefs).not.toContain('/?sample_id=1')
  })
})
