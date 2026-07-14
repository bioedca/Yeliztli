/**
 * Issue #1775 — gnomAD's NFE population is explicit on the gene-detail chart.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const BRCA1_WITH_POPULATION_AF = {
  gene_symbol: 'BRCA1',
  uniprot: null,
  uniprot_error: null,
  phenotypes: [],
  literature: [],
  literature_errors: [],
  variants: [],
  population_af: [
    {
      rsid: 'rs16942',
      hgvs_protein: null,
      gnomad_af_global: 0.42,
      gnomad_af_afr: 0.3,
      gnomad_af_amr: 0.4,
      gnomad_af_asj: 0.45,
      gnomad_af_eas: 0.1,
      gnomad_af_eur: 0.44,
      gnomad_af_fin: 0.38,
      gnomad_af_sas: 0.35,
    },
  ],
}

test('labels gnomAD NFE separately from Finnish on the gene-detail chart', async ({ page }) => {
  await page.route('**/api/genes/BRCA1**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(BRCA1_WITH_POPULATION_AF),
    }),
  )

  await page.goto('/genes/BRCA1?sample_id=1')
  await waitForReactHydration(page)

  const chart = page.getByTestId('population-af-chart')
  await expect(chart).toBeVisible()
  const nfeLabel = chart.getByText('European (non-Finnish)', { exact: true })
  await expect(nfeLabel).toBeVisible()
  await expect(chart.getByText('Finnish', { exact: true })).toBeVisible()

  const [chartBox, labelBox] = await Promise.all([chart.boundingBox(), nfeLabel.boundingBox()])
  if (!chartBox || !labelBox) throw new Error('Expected visible chart and NFE label bounds')
  expect(labelBox.x).toBeGreaterThanOrEqual(chartBox.x)
  expect(labelBox.x + labelBox.width).toBeLessThanOrEqual(chartBox.x + chartBox.width)
})
