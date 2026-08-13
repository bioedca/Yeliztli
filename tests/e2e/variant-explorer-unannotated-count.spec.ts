/**
 * #1978: the Variant Explorer's "Show unannotated (N)" toggle rendered its badge
 * from the sample TOTAL, not the unannotated count — so a fully-annotated sample
 * advertised its entire variant set as unannotated ("(677,436)") and toggling
 * revealed nothing. The badge now counts `annotation_coverage:null` via the count
 * endpoint, and the toggle is disabled when that count is zero.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const variantPage = {
  items: [
    {
      rsid: 'rs1978',
      chrom: '1',
      pos: 1978,
      genotype: 'AG',
      ref: 'A',
      alt: 'G',
      zygosity: 'het',
      gene_symbol: 'BRCA1',
      consequence: 'missense_variant',
      clinvar_significance: 'Pathogenic',
      clinvar_review_stars: 2,
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
      pos_grch38: 50_197,
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

/**
 * Stub the (filter-aware) count endpoint. `total` is the unfiltered call, and
 * `unannotated` is returned for the `annotation_coverage:null` filter the badge's
 * hook uses; the `notnull` (annotated) call returns the difference.
 */
async function stubCounts(
  page: import('@playwright/test').Page,
  { total, unannotated }: { total: number; unannotated: number },
) {
  await page.route(/\/api\/variants\/count(\?|$)/, (route) => {
    const url = decodeURIComponent(route.request().url())
    if (url.includes('annotation_coverage:notnull')) {
      return route.fulfill(jsonRoute({ total: total - unannotated, filtered: true }))
    }
    if (url.includes('annotation_coverage:null')) {
      return route.fulfill(jsonRoute({ total: unannotated, filtered: true }))
    }
    return route.fulfill(jsonRoute({ total, filtered: false }))
  })
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await page.route(/\/api\/column-presets(\?|\/|$)/, (route) =>
    route.fulfill(jsonRoute({ presets: [] })),
  )
  await page.route(/\/api\/tags(\?|$)/, (route) => route.fulfill(jsonRoute([])))
  await page.route(/\/api\/variants\/chromosomes(\?|$)/, (route) =>
    route.fulfill(jsonRoute([{ chrom: '1', count: 1 }])),
  )
  await page.route(/\/api\/variants(\?[^/]*)?$/, (route) => route.fulfill(jsonRoute(variantPage)))
  await page.route(/\/api\/samples\/\d+\/merge-provenance$/, (route) =>
    route.fulfill(jsonRoute(null)),
  )
  await page.route(/\/api\/watches(\?|$)/, (route) => route.fulfill(jsonRoute([])))
})

test('badge reads (0) and the toggle is disabled for a fully-annotated sample (#1978)', async ({
  page,
}) => {
  await stubCounts(page, { total: 677_436, unannotated: 0 })
  await page.goto('/variants?sample_id=1')
  await waitForReactHydration(page)

  const toggle = page.getByRole('button', { name: 'Show unannotated variants' })
  await expect(toggle).toContainText('Show unannotated (0)')
  await expect(toggle).not.toContainText('677,436')
  await expect(toggle).toBeDisabled()
})

test('badge shows the real unannotated count and stays enabled when > 0 (#1978)', async ({
  page,
}) => {
  await stubCounts(page, { total: 100, unannotated: 7 })
  await page.goto('/variants?sample_id=1')
  await waitForReactHydration(page)

  const toggle = page.getByRole('button', { name: 'Show unannotated variants' })
  await expect(toggle).toContainText('Show unannotated (7)')
  await expect(toggle).not.toContainText('(100)')
  await expect(toggle).toBeEnabled()
})
