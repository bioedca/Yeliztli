/**
 * Issue #2058 — the Variant Explorer's search box must query the server over
 * the whole sample. It used to filter only the pages already loaded, so a gene
 * or rsID that had not been paged in reported "No variants match".
 */

import { test, expect, type Page } from '@playwright/test'
import { bypassSetup, mockFreshSampleState, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

function variantRow(overrides: Record<string, unknown>) {
  return {
    rsid: 'rs100',
    chrom: '1',
    pos: 1000,
    genotype: 'AG',
    ref: 'A',
    alt: 'G',
    zygosity: 'het',
    gene_symbol: 'TP53',
    consequence: 'missense_variant',
    clinvar_significance: null,
    clinvar_review_stars: null,
    gnomad_af_global: 0.001,
    rare_flag: true,
    cadd_phred: 25.5,
    sift_score: 0.01,
    sift_pred: 'D',
    polyphen2_hsvar_score: 0.99,
    polyphen2_hsvar_pred: 'D',
    revel: 0.85,
    annotation_coverage: 0b111111,
    evidence_conflict: false,
    ensemble_pathogenic: false,
    chrom_grch38: '1',
    pos_grch38: 51_000,
    tags: [],
    source: '',
    concordance: '',
    ...overrides,
  }
}

const page1 = {
  items: [variantRow({ rsid: 'rs100', pos: 1000 }), variantRow({ rsid: 'rs101', pos: 2000 })],
  next_cursor_chrom: null,
  next_cursor_pos: null,
  has_more: false,
  limit: 100,
}
const brca1Page = {
  ...page1,
  items: [variantRow({ rsid: 'rs80357906', chrom: '17', pos: 41_245_466, gene_symbol: 'BRCA1' })],
}

async function stubExplorer(page: Page): Promise<string[]> {
  const listUrls: string[] = []
  await bypassSetup(page)
  await mockFreshSampleState(page)
  await page.route(/\/api\/column-presets(\?|\/|$)/, (route) => route.fulfill(jsonRoute({ presets: [] })))
  await page.route(/\/api\/tags(\?|$)/, (route) => route.fulfill(jsonRoute([])))
  await page.route(/\/api\/watches(\?|$)/, (route) => route.fulfill(jsonRoute([])))
  await page.route(/\/api\/samples\/\d+\/merge-provenance$/, (route) => route.fulfill(jsonRoute(null)))
  await page.route(/\/api\/variants\/chromosomes(\?|$)/, (route) =>
    route.fulfill(jsonRoute([{ chrom: '1', count: 2 }, { chrom: '17', count: 1 }])),
  )
  await page.route(/\/api\/variants\/count(\?|$)/, (route) => {
    const url = new URL(route.request().url())
    const total = url.searchParams.get('search') === 'BRCA1' ? 1 : 2
    return route.fulfill(jsonRoute({ total, filtered: url.searchParams.has('search') }))
  })
  await page.route(/\/api\/variants(\?[^/]*)?$/, (route) => {
    const url = new URL(route.request().url())
    listUrls.push(url.search)
    return route.fulfill(jsonRoute(url.searchParams.get('search') === 'BRCA1' ? brca1Page : page1))
  })
  return listUrls
}

test('typing a gene that is not in the loaded page queries the server and shows it (#2058)', async ({ page }) => {
  const listUrls = await stubExplorer(page)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/variants?sample_id=1')
  await waitForReactHydration(page)
  await expect(page.getByText('rs100')).toBeVisible()

  await page.getByLabel('Search variants by rsid or gene').fill('BRCA1')

  await expect(page.getByText('rs80357906')).toBeVisible()
  await expect(page.getByText('rs100')).toHaveCount(0)
  await expect(page.getByText(/No variants match/i)).toHaveCount(0)
  const searched = listUrls.filter((search) => new URLSearchParams(search).get('search') === 'BRCA1')
  expect(searched).toHaveLength(1)
  expect(new URLSearchParams(searched[0]).has('cursor_chrom')).toBe(false)
})
