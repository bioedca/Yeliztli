/** #2465: the existing confirmation notice follows current primary P/LP results. */
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { bypassSetup, mockFreshSampleState } from './helpers'

const json = (body: unknown) => ({ contentType: 'application/json', body: JSON.stringify(body) })
const finding = {
  rsid: 'synthetic-confirmation-1', gene_symbol: 'SYNTH1', category: 'clinvar_pathogenic',
  evidence_level: 2, finding_text: 'Synthetic finding', zygosity: 'het',
  clinvar_significance: 'Pathogenic', conditions: null, detail: {},
}
const variant = {
  rsid: 'synthetic-search-1', chrom: '1', pos: 100, ref: 'A', alt: 'G', genotype: 'AG',
  zygosity: 'het', gene_symbol: 'SYNTH1', consequence: 'missense_variant',
  hgvs_coding: null, hgvs_protein: null, gnomad_af_global: 0.001,
  gnomad_af_afr: null, gnomad_af_amr: null, gnomad_af_asj: null, gnomad_af_eas: null,
  gnomad_af_eur: null, gnomad_af_fin: null, gnomad_af_sas: null, is_novel: false,
  clinvar_review_stars: 0, clinvar_accession: null, clinvar_conditions: null,
  cadd_phred: null, revel: null, deleterious_count: null, deleterious_total_assessed: null,
  ensemble_pathogenic: false, evidence_conflict: false, evidence_level: 2,
  disease_name: null, inheritance_pattern: null,
}
const noticeName = 'Clinical confirmation required before acting on these results'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await mockFreshSampleState(page)
  await page.route(/\/api\/annotation\/active\/\d+$/, (route) =>
    route.fulfill({ ...json({ detail: 'No active job' }), status: 404 }),
  )
  await page.route('**/api/panels', (route) => route.fulfill(json({ items: [] })))
  await page.route('**/api/individuals', (route) => route.fulfill(json([])))
  await page.route('**/api/samples', (route) => route.fulfill(json([1, 2].map((id) => ({
    id, name: `Synthetic sample ${id}`, db_path: `synthetic-${id}.db`,
    file_format: null, file_hash: null, notes: null, date_collected: null,
    source: null, extra: null, created_at: null, updated_at: null,
  })))))
})

test('confirmation is accessible, non-dismissible and follows filter results', async ({ page }) => {
  await page.route('**/api/analysis/rare-variants/findings**', (route) =>
    route.fulfill(json({ items: [finding], total: 1 })),
  )
  const searches = [
    { significance: 'Benign', count: 0 },
    { significance: 'Likely pathogenic', count: 1 },
    { significance: 'Conflicting classifications of pathogenicity', count: 0 },
    { significance: null, count: 0 },
  ]
  const requestedGenes: unknown[] = []
  await page.route('**/api/analysis/rare-variants/search**', (route) => {
    const filters = route.request().postDataJSON()
    requestedGenes.push(filters.gene_symbols)
    const current = searches[requestedGenes.length - 1]
    return route.fulfill(json({
      items: current.significance ? [{ ...variant, clinvar_significance: current.significance }] : [],
      total: current.significance ? 1 : 0, total_variants_scanned: 10,
      novel_count: 0, pathogenic_count: current.count,
      genes_with_findings: current.significance ? ['SYNTH1'] : [], filters_applied: filters,
    }))
  })
  await page.goto('/rare-variants?sample_id=1')
  await expect(page.getByTestId('finding-row')).toHaveCount(1)
  const notice = page.getByRole('note', { name: noticeName })
  await expect(notice).toBeVisible()
  await expect(notice).toContainText('CLIA/accredited laboratory')
  await expect(notice.getByRole('button')).toHaveCount(0)
  const accessibility = await new AxeBuilder({ page })
    .include('[data-testid="clia-confirmation-gate"]')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  expect(accessibility.violations).toEqual([])

  await page.getByTestId('gene-panel-input').fill('SYNTH2')
  await page.getByTestId('search-button').focus()
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('result-row')).toHaveCount(1)
  await expect(page.getByTestId('result-row')).toContainText('Benign')
  await expect(notice).toHaveCount(0)
  expect(requestedGenes).toEqual([['SYNTH2']])

  await page.getByTestId('gene-panel-input').clear()
  await page.getByTestId('search-button').click()
  await expect(page.getByTestId('result-row')).toContainText('Likely pathogenic')
  await expect(notice).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(notice).toBeVisible()

  await page.getByTestId('search-button').click()
  await expect(page.getByTestId('result-row')).toContainText('Conflicting classifications')
  await expect(notice).toHaveCount(0)
  await page.getByTestId('search-button').click()
  await expect(page.getByTestId('no-results')).toBeVisible()
  await expect(notice).toHaveCount(0)
  expect(requestedGenes).toEqual([['SYNTH2'], null, null, null])
})

test('sample switch clears the old findings and notice while the next sample loads', async ({ page }) => {
  let finish!: () => void
  const pending = new Promise<void>((resolve) => { finish = resolve })
  let requestedSecondSample = false
  await page.route('**/api/analysis/rare-variants/findings**', async (route) => {
    if (new URL(route.request().url()).searchParams.get('sample_id') === '1') {
      await route.fulfill(json({ items: [finding], total: 1 }))
    } else {
      requestedSecondSample = true
      await pending
      await route.fulfill(json({ items: [], total: 0 }))
    }
  })
  await page.goto('/rare-variants?sample_id=1')
  await expect(page.getByRole('note', { name: noticeName })).toBeVisible()
  await page.getByRole('button', { name: 'Synthetic sample 1, switch sample' }).click()
  await page.getByRole('treeitem', { name: 'Synthetic sample 2 Unknown format', exact: true }).click()
  await expect.poll(() => requestedSecondSample).toBe(true)
  await expect(page.getByTestId('finding-row')).toHaveCount(0)
  await expect(page.getByRole('note', { name: noticeName })).toHaveCount(0)
  finish()
  await expect(page.getByText('No rare variant findings yet.')).toBeVisible()
  await expect(page.getByRole('note', { name: noticeName })).toHaveCount(0)
})
