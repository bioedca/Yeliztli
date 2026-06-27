/**
 * Issue #1084 — SDHD cancer findings must surface the parent-of-origin caveat.
 */

import { expect, test } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const SDHD_CAVEAT =
  'Clinical caveat: SDHD has a parent-of-origin effect; disease penetrance is ' +
  'primarily associated with paternal inheritance. Array data do not determine ' +
  'parent of origin, so clinical/genetic confirmation and family-history review are needed.'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

test('Cancer page renders SDHD clinical caveat on the card and detail panel (#1084)', async ({
  page,
}) => {
  await page.route('**/api/analysis/cancer/variants**', (route) =>
    route.fulfill(
      jsonRoute({
        items: [
          {
            rsid: 'rs28934575',
            gene_symbol: 'SDHD',
            genotype: 'C/T',
            zygosity: 'het',
            clinvar_significance: 'Pathogenic',
            clinvar_accession: 'VCV000013575',
            clinvar_review_stars: 2,
            clinvar_conditions: 'Paraganglioma-Pheochromocytoma Syndrome',
            syndromes: ['Paraganglioma-Pheochromocytoma Syndrome'],
            cancer_types: ['Paraganglioma', 'Pheochromocytoma'],
            inheritance: 'AD',
            clinical_caveat: SDHD_CAVEAT,
            evidence_level: 4,
            cross_links: [],
            pmids: ['20301715', '15064708', '23493432'],
          },
        ],
        total: 1,
      }),
    ),
  )
  await page.route('**/api/analysis/cancer/prs**', (route) =>
    route.fulfill(
      jsonRoute({
        items: [],
        total: 0,
        sufficient_count: 0,
        insufficient_traits: [],
      }),
    ),
  )
  await page.route('**/api/analysis/cancer/disclaimer', (route) =>
    route.fulfill(
      jsonRoute({
        title: 'Cancer module disclaimer',
        text: 'For research and educational use only.',
      }),
    ),
  )
  await page.route('**/api/analysis/cancer/absolute-risk**', (route) =>
    route.fulfill(
      jsonRoute({
        consented: false,
        opt_in_required: true,
        opt_in_prompt: 'Show optional absolute-risk context.',
        disclaimer: 'For research and educational use only.',
      }),
    ),
  )

  await page.goto('/cancer?sample_id=1')
  await waitForReactHydration(page)

  const card = page.getByTestId('cancer-variant-card').filter({ hasText: 'SDHD' })
  await expect(card).toBeVisible()
  await expect(page.getByTestId('cancer-clinical-caveat')).toContainText('parent-of-origin')
  await expect(page.getByTestId('cancer-clinical-caveat')).toContainText('paternal inheritance')

  await card.click()

  const panel = page.getByTestId('variant-detail-panel')
  await expect(panel).toBeVisible()
  await expect(panel.getByTestId('cancer-clinical-caveat-panel')).toContainText(
    'parent-of-origin',
  )
  await expect(panel.getByTestId('cancer-clinical-caveat-panel')).toContainText(
    'paternal inheritance',
  )
})
