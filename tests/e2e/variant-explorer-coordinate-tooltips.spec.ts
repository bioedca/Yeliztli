/**
 * Issue #1665 — Variant Explorer coordinate headers and GRCh38 toggle must explain
 * native GRCh37 coordinates, computational GRCh38 liftover, and blank lifted cells.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const grch37Tooltip =
  'Native GRCh37/hg19 coordinate stored for this sample; use these columns with GRCh37/hg19 tools.'
const grch38Tooltip =
  'Computational GRCh38/hg38 liftover from the native GRCh37 coordinate; blank means the position could not be lifted over, including MT/mitochondrial variants, which are never lifted.'
const toggleTooltip =
  'Show computational GRCh38/hg38 liftover columns. Default coordinate columns are native GRCh37/hg19; blank GRCh38 cells mean liftover was unavailable, including MT/mitochondrial variants.'

const variantPage = {
  items: [
    {
      rsid: 'rs1665',
      chrom: '1',
      pos: 1665,
      genotype: 'AG',
      ref: 'A',
      alt: 'G',
      zygosity: 'het',
      gene_symbol: 'BRCA1',
      consequence: 'missense_variant',
      clinvar_significance: 'Uncertain significance',
      clinvar_review_stars: 1,
      gnomad_af_global: 0.001,
      rare_flag: false,
      cadd_phred: 12.5,
      sift_score: null,
      sift_pred: null,
      polyphen2_hsvar_score: null,
      polyphen2_hsvar_pred: null,
      revel: null,
      annotation_coverage: 0b111111,
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
    route.fulfill(jsonRoute([{ chrom: '1', count: 1 }])),
  )
  await page.route(/\/api\/variants(\?[^/]*)?$/, (route) =>
    route.fulfill(jsonRoute(variantPage)),
  )
  await page.route(/\/api\/samples\/\d+\/merge-provenance$/, (route) =>
    route.fulfill(jsonRoute({ detail: 'Sample is not a merged sample' }, 404)),
  )
  await page.route(/\/api\/watches(\?|$)/, (route) => route.fulfill(jsonRoute([])))
})

test('Variant Explorer explains GRCh37 headers and GRCh38 liftover columns (#1665)', async ({
  page,
}) => {
  await page.goto('/variants?sample_id=1')
  await waitForReactHydration(page)

  await expect(page.getByText('rs1665')).toBeVisible()
  await expect(page.getByText('Chr (GRCh37)')).toHaveAttribute('title', grch37Tooltip)
  await expect(page.getByText('Position (GRCh37)')).toHaveAttribute('title', grch37Tooltip)

  const toggle = page.getByRole('button', { name: /show grch38 coordinates/i })
  await expect(toggle).toHaveAttribute('title', toggleTooltip)
  await expect(toggle).toHaveAttribute('aria-describedby', 'variant-table-grch38-toggle-help')
  await expect(page.locator('#variant-table-grch38-toggle-help')).toHaveText(toggleTooltip)

  await toggle.click()

  await expect(page.getByText('Chr (GRCh38)')).toHaveAttribute('title', grch38Tooltip)
  await expect(page.getByText('Pos (GRCh38)')).toHaveAttribute('title', grch38Tooltip)
})
