/**
 * Issue #710 — Variant Explorer table tag pills must use the user's configured
 * tag colors, matching the tag filter dropdown. Unknown tag names still fall
 * back to the neutral gray pill color.
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
      rsid: 'rs710',
      chrom: '1',
      pos: 710,
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
      pos_grch38: 50_710,
      tags: ['Pathogenic tag', 'Reviewed', 'Unknown'],
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
  await page.route(/\/api\/tags(\?|$)/, (route) =>
    route.fulfill(
      jsonRoute([
        {
          id: 1,
          name: 'Pathogenic tag',
          color: '#dc2626',
          is_predefined: false,
          created_at: null,
          variant_count: 1,
        },
        {
          id: 2,
          name: 'Reviewed',
          color: '#16a34a',
          is_predefined: false,
          created_at: null,
          variant_count: 1,
        },
      ]),
    ),
  )
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

test('Variant Explorer table tag pills use configured tag colors (#710)', async ({ page }) => {
  await page.goto('/variants?sample_id=1')
  await waitForReactHydration(page)

  await expect(page.getByTitle('Pathogenic tag')).toHaveCSS('background-color', 'rgb(220, 38, 38)')
  await expect(page.getByTitle('Reviewed')).toHaveCSS('background-color', 'rgb(22, 163, 74)')
  await expect(page.getByTitle('Unknown')).toHaveCSS('background-color', 'rgb(107, 114, 128)')
})
