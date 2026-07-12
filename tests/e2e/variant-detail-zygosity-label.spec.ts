/**
 * Issue #1748 — variant detail surfaces use the API's ploidy-aware
 * zygosity_label instead of exposing the stored compact zygosity enum.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const variantDetail = {
  rsid: 'rs1748',
  chrom: 'X',
  pos: 10_000_000,
  ref: 'A',
  alt: 'G',
  genotype: 'G',
  zygosity: 'hom_alt',
  zygosity_label: 'Hemizygous',
  gene_symbol: 'GENEX',
  transcript_id: null,
  consequence: 'missense_variant',
  hgvs_coding: null,
  hgvs_protein: null,
  strand: '+',
  exon_number: null,
  intron_number: null,
  mane_select: null,
  clinvar_significance: null,
  clinvar_review_stars: null,
  clinvar_accession: null,
  clinvar_conditions: null,
  gnomad_af_global: null,
  rare_flag: false,
  ultra_rare_flag: false,
  cadd_phred: null,
  sift_score: null,
  sift_pred: null,
  polyphen2_hsvar_score: null,
  polyphen2_hsvar_pred: null,
  revel: null,
  deleterious_count: null,
  deleterious_total_assessed: null,
  ensemble_pathogenic: false,
  evidence_conflict_detail: null,
  transcripts: [],
  gene_phenotypes: [],
}

const variantSummary = {
  items: [
    {
      rsid: variantDetail.rsid,
      chrom: variantDetail.chrom,
      pos: variantDetail.pos,
      genotype: variantDetail.genotype,
      ref: variantDetail.ref,
      alt: variantDetail.alt,
      zygosity: variantDetail.zygosity,
      gene_symbol: variantDetail.gene_symbol,
      consequence: variantDetail.consequence,
      clinvar_significance: null,
      clinvar_review_stars: null,
      gnomad_af_global: null,
      rare_flag: false,
      cadd_phred: null,
      sift_score: null,
      sift_pred: null,
      polyphen2_hsvar_score: null,
      polyphen2_hsvar_pred: null,
      revel: null,
      annotation_coverage: 0,
      evidence_conflict: false,
      ensemble_pathogenic: false,
      chrom_grch38: null,
      pos_grch38: null,
      tags: [],
      source: '',
      concordance: '',
    },
  ],
  next_cursor_chrom: null,
  next_cursor_pos: null,
  has_more: false,
  limit: 100,
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await page.route(/\/api\/column-presets(\?|\/|$)/, (route) =>
    route.fulfill(jsonRoute({ presets: [] })),
  )
  await page.route(/\/api\/tags(\?|$)/, (route) => route.fulfill(jsonRoute([])))
  await page.route(/\/api\/variants\/count(\?|$)/, (route) =>
    route.fulfill(jsonRoute({ total: 1, filtered: false })),
  )
  await page.route(/\/api\/variants\/chromosomes(\?|$)/, (route) =>
    route.fulfill(jsonRoute([{ chrom: 'X', count: 1 }])),
  )
  await page.route(/\/api\/variants\/rs1748(\?|$)/, (route) =>
    route.fulfill(jsonRoute(variantDetail)),
  )
  await page.route(/\/api\/variants(\?[^/]*)?$/, (route) =>
    route.fulfill(jsonRoute(variantSummary)),
  )
  await page.route(/\/api\/samples\/\d+\/merge-provenance$/, (route) =>
    route.fulfill(jsonRoute({ detail: 'Sample is not a merged sample' }, 404)),
  )
  await page.route(/\/api\/watches(\?|$)/, (route) => route.fulfill(jsonRoute([])))
})

test('uses the ploidy-aware label in the side panel and full detail page', async ({ page }) => {
  await page.goto('/variants?sample_id=1')
  await waitForReactHydration(page)
  await page.getByText('rs1748', { exact: true }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Hemizygous')
  await expect(dialog).not.toContainText('hom_alt')

  await dialog.getByRole('link', { name: /open full detail/i }).click()

  const overview = page.getByTestId('tab-overview')
  await expect(overview).toContainText('Hemizygous')
  await expect(overview).not.toContainText('hom_alt')
})
