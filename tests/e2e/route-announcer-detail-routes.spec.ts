/**
 * Issue #1887 — dynamic detail routes announce their page type after a real
 * client-side navigation instead of falling through to the generic "Page".
 */

import { expect, test } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const FINDING = {
  id: 1,
  module: 'cancer',
  category: 'risk',
  evidence_level: 4,
  gene_symbol: 'BRCA1',
  rsid: 'rs1669',
  finding_text: 'BRCA1 route-announcer regression finding',
  phenotype: null,
  conditions: null,
  zygosity: 'heterozygous',
  clinvar_significance: 'pathogenic',
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
  created_at: '2026-07-15T00:00:00Z',
}

const FINDINGS_SUMMARY = {
  total_findings: 1,
  modules: [],
  high_confidence_findings: [FINDING],
}

const GENE_DETAIL = {
  gene_symbol: 'BRCA1',
  uniprot: null,
  uniprot_error: null,
  phenotypes: [],
  literature: [],
  literature_errors: [],
  variants: [],
  population_af: [],
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

test('announces a gene detail route after client-side navigation', async ({ page }) => {
  await page.route('**/api/analysis/findings**', (route) => {
    const body = route.request().url().includes('/findings/summary')
      ? FINDINGS_SUMMARY
      : [FINDING]
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    })
  })
  await page.route('**/api/genes/BRCA1**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(GENE_DETAIL),
    }),
  )

  await page.goto('/findings?sample_id=1')
  await waitForReactHydration(page)
  await expect(page.getByText(FINDING.finding_text)).toBeVisible()

  await page.getByRole('link', { name: 'BRCA1', exact: true }).click()

  await expect(page).toHaveURL(/\/genes\/BRCA1\?sample_id=1$/)
  await expect(page.getByTestId('gene-symbol')).toHaveText('BRCA1')
  await expect(page.getByTestId('route-announcer')).toHaveText(
    'Navigated to Gene Detail',
  )
})
