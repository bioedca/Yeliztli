/**
 * Issue #1526 — Rare Variant Finder must not fetch and render all stored
 * findings for large samples. The table requests a bounded first page and
 * expands the bound via "Load more"; exports remain the full-data path.
 */

import { expect, test } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const INITIAL_LIMIT = 200
const TOTAL_FINDINGS = 66_770

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

function finding(index: number) {
  return {
    rsid: `rs_rare_${index}`,
    gene_symbol: `GENE${index % 20}`,
    category: 'rare',
    evidence_level: 1,
    finding_text: `Rare finding ${index}`,
    zygosity: 'het',
    clinvar_significance: null,
    clinvar_low_penetrance_or_risk_allele: false,
    conditions: null,
    detail: {},
  }
}

function rareVariant(index: number) {
  return {
    rsid: `rs_search_${index}`,
    chrom: '1',
    pos: 1_000_000 + index,
    ref: 'A',
    alt: 'G',
    genotype: 'AG',
    zygosity: 'het',
    gene_symbol: `GENE${index % 20}`,
    consequence: 'missense_variant',
    hgvs_coding: null,
    hgvs_protein: null,
    gnomad_af_global: 0.0001,
    gnomad_af_afr: null,
    gnomad_af_amr: null,
    gnomad_af_asj: null,
    gnomad_af_eas: null,
    gnomad_af_eur: null,
    gnomad_af_fin: null,
    gnomad_af_sas: null,
    gnomad_source_status: 'covered',
    is_novel: false,
    clinvar_significance: null,
    clinvar_review_stars: null,
    clinvar_accession: null,
    clinvar_conditions: null,
    cadd_phred: null,
    revel: null,
    ensemble_pathogenic: false,
    evidence_conflict: false,
    evidence_level: 1,
    disease_name: null,
    inheritance_pattern: null,
  }
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

test('Rare Variants previous findings requests and renders a bounded page (#1526)', async ({
  page,
}) => {
  const requestedLimits: number[] = []

  await page.route('**/api/analysis/rare-variants/findings**', async (route) => {
    const url = new URL(route.request().url())
    const limit = Number(url.searchParams.get('limit') ?? TOTAL_FINDINGS)
    requestedLimits.push(limit)

    await route.fulfill(
      jsonRoute({
        items: Array.from({ length: Math.min(limit, TOTAL_FINDINGS) }, (_, i) =>
          finding(i + 1),
        ),
        total: TOTAL_FINDINGS,
      }),
    )
  })

  await page.goto('/rare-variants?sample_id=1')
  await waitForReactHydration(page)

  expect(requestedLimits).toEqual([INITIAL_LIMIT])
  await expect(page.getByText('66770 findings from last analysis run')).toBeVisible()
  await expect(page.getByTestId('finding-row')).toHaveCount(INITIAL_LIMIT)
  await expect(page.getByText('Showing the top 200 of 66770 findings')).toBeVisible()

  await page.getByRole('button', { name: 'Load more findings' }).click()

  await expect.poll(() => requestedLimits).toEqual([INITIAL_LIMIT, INITIAL_LIMIT * 2])
  await expect(page.getByTestId('finding-row')).toHaveCount(INITIAL_LIMIT * 2)
  await expect(page.getByText('Showing the top 400 of 66770 findings')).toBeVisible()
})

test('Rare Variants search results render a bounded table for broad searches (#1526)', async ({
  page,
}) => {
  const searchTotal = 6_000
  const requestedSearches: string[] = []

  await page.route('**/api/panels', (route) => route.fulfill(jsonRoute({ items: [] })))
  await page.route('**/api/analysis/rare-variants/findings**', (route) =>
    route.fulfill(jsonRoute({ items: [], total: 0 })),
  )
  await page.route('**/api/analysis/rare-variants/search**', async (route) => {
    requestedSearches.push(route.request().method())
    await route.fulfill(
      jsonRoute({
        items: Array.from({ length: searchTotal }, (_, i) => rareVariant(i + 1)),
        total: searchTotal,
        total_variants_scanned: searchTotal,
        novel_count: 0,
        pathogenic_count: 0,
        genes_with_findings: ['GENE1'],
        filters_applied: {
          gene_symbols: null,
          af_threshold: 0.01,
          consequences: null,
          clinvar_significance: null,
          include_novel: true,
          zygosity: null,
        },
      }),
    )
  })

  await page.goto('/rare-variants?sample_id=1')
  await waitForReactHydration(page)
  await page.getByTestId('search-button').click()

  await expect.poll(() => requestedSearches).toEqual(['POST'])
  await expect(page.getByTestId('total-found')).toHaveText('6,000')
  await expect(page.getByTestId('result-row')).toHaveCount(INITIAL_LIMIT)
  await expect(page.getByText('Showing the top 200 of 6000 variants')).toBeVisible()

  await page.getByRole('button', { name: 'Load more results' }).click()

  await expect(page.getByTestId('result-row')).toHaveCount(INITIAL_LIMIT * 2)
  await expect(page.getByText('Showing the top 400 of 6000 variants')).toBeVisible()
})
