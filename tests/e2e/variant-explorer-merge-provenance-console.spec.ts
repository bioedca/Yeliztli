/** Issue #2037 — an existing unmerged sample is an ordinary nullable response,
 * not a failed resource request in the Variant Explorer console. */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const variantPage = {
  items: [
    {
      rsid: 'rs2037',
      chrom: '1',
      pos: 2037,
      genotype: 'AG',
      ref: 'A',
      alt: 'G',
      zygosity: 'het',
      gene_symbol: 'TEST1',
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
    route.fulfill(jsonRoute(null)),
  )
  await page.route(/\/api\/annotation\/active\/\d+$/, (route) =>
    route.fulfill(jsonRoute({ job_id: null, status: null })),
  )
  await page.route(/\/api\/watches(\?|$)/, (route) => route.fulfill(jsonRoute([])))
})

test('unmerged Variant Explorer loads with nullable provenance and no console errors (#2037)', async ({
  page,
}) => {
  const consoleErrors: string[] = []
  const failedResponses: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('response', (response) => {
    if (response.status() >= 400) {
      failedResponses.push(`${response.status()} ${response.url()}`)
    }
  })
  const provenanceResponse = page.waitForResponse((response) =>
    response.url().endsWith('/api/samples/1/merge-provenance'),
  )

  await page.goto('/variants?sample_id=1')
  await waitForReactHydration(page)

  const response = await provenanceResponse
  expect(response.status()).toBe(200)
  expect(await response.json()).toBeNull()
  await expect(page.getByText('rs2037')).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Source' })).toHaveCount(0)
  await expect(page.getByRole('columnheader', { name: 'Concordance' })).toHaveCount(0)
  expect(failedResponses).toEqual([])
  expect(
    consoleErrors,
    `Unexpected console errors:\n${consoleErrors.join('\n')}\nFailed responses:\n${failedResponses.join('\n')}`,
  ).toEqual([])
})
