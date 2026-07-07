/**
 * Issue #1691 — compact CADD/REVEL table headers must explain score direction,
 * scale, and display-threshold semantics wherever the bare labels appear.
 */

import { test, expect, type Page } from '@playwright/test'
import { CADD_TOOLTIP, REVEL_TOOLTIP } from '../../frontend/src/lib/inSilicoScoreInfo'
import { bypassSetup, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const variantRow = {
  rsid: 'rs1691',
  chrom: '1',
  pos: 1691,
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
  cadd_phred: 24.7,
  sift_score: null,
  sift_pred: null,
  polyphen2_hsvar_score: null,
  polyphen2_hsvar_pred: null,
  revel: 0.63,
  annotation_coverage: 0b111111,
  evidence_conflict: false,
  ensemble_pathogenic: false,
  chrom_grch38: null,
  pos_grch38: null,
  tags: [],
  source: '',
  concordance: '',
}

const variantPage = {
  items: [variantRow],
  next_cursor_chrom: null,
  next_cursor_pos: null,
  has_more: false,
  limit: 100,
}

async function stubVariantExplorer(page: Page) {
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
}

async function stubQueryBuilder(page: Page) {
  await page.route(/\/api\/query\/fields$/, (route) =>
    route.fulfill(
      jsonRoute({
        fields: [
          { name: 'rsid', type: 'text', label: 'rsID' },
          { name: 'chrom', type: 'text', label: 'Chromosome' },
          { name: 'pos', type: 'integer', label: 'Position' },
          { name: 'cadd_phred', type: 'number', label: 'CADD' },
          { name: 'revel', type: 'number', label: 'REVEL' },
        ],
        operators: ['=', '!=', '<', '>', '<=', '>=', 'contains', 'null', 'notNull'],
      }),
    ),
  )
  await page.route(/\/api\/saved-queries(\?|$)/, (route) =>
    route.fulfill(jsonRoute({ queries: [] })),
  )
  await page.route(/\/api\/query$/, (route) => {
    if (route.request().method() !== 'POST') {
      return route.fallback()
    }
    return route.fulfill(jsonRoute({ ...variantPage, total_matching: 1 }))
  })
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

test('Variant Explorer CADD and REVEL headers use the shared score tooltips (#1691)', async ({
  page,
}) => {
  await stubVariantExplorer(page)
  await page.goto('/variants?sample_id=1')
  await waitForReactHydration(page)

  await expect(page.getByText('rs1691')).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'CADD' })).toHaveAttribute(
    'title',
    CADD_TOOLTIP,
  )
  await expect(page.getByRole('columnheader', { name: 'REVEL' })).toHaveAttribute(
    'title',
    REVEL_TOOLTIP,
  )
})

test('Query Builder results CADD and REVEL headers use the shared score tooltips (#1691)', async ({
  page,
}) => {
  await stubQueryBuilder(page)
  await page.goto('/query-builder?sample_id=1')
  await waitForReactHydration(page)

  await page.getByRole('button', { name: '+ Rule' }).click()
  const runButton = page.getByTestId('run-query-btn')
  await expect(runButton).toBeEnabled()
  await runButton.click()

  await expect(page.getByText('rs1691')).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'CADD' })).toHaveAttribute(
    'title',
    CADD_TOOLTIP,
  )
  await expect(page.getByRole('columnheader', { name: 'REVEL' })).toHaveAttribute(
    'title',
    REVEL_TOOLTIP,
  )
})
