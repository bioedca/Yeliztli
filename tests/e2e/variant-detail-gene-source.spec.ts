/** Synthetic gene-source presentation and accessibility controls (#2464). */
import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { bypassSetup, mockFreshSampleState, waitForReactHydration } from './helpers'

for (const source of ['clinvar', 'vep', null] as const) {
  test(`identifies gene source ${source ?? 'unknown'} without inventing a transcript`, async ({ page }) => {
    await bypassSetup(page)
    await mockFreshSampleState(page)
    await page.route(/\/api\/(samples|individuals)(\?|$)/, route => route.fulfill({ json: [] }))
    await page.route('**/api/annotation/active/1', route => route.fulfill({ status: 404 }))
    await page.route('**/api/variants/rsYELIZTLI2464**', route => route.fulfill({
      json: {
        rsid: 'rsYELIZTLI2464', chrom: '1', pos: 992464,
        ref: 'A', alt: 'G', genotype: 'AG', zygosity: 'het',
        zygosity_label: 'Heterozygous', gene_symbol: 'YELIZTLI2464', gene_source: source,
        transcript_id: null, consequence: null, hgvs_coding: null, hgvs_protein: null,
        strand: null, exon_number: null, intron_number: null, mane_select: false,
        clinvar_significance: null, clinvar_review_stars: null,
        clinvar_accession: null, clinvar_conditions: null,
        gnomad_af_global: null, gnomad_source_status: null,
        rare_flag: false, ultra_rare_flag: false, ensemble_pathogenic: false,
        evidence_conflict: false, evidence_conflict_detail: null,
        annotation_coverage: source === 'vep' ? 1 : 2,
        transcripts: [], gene_phenotypes: [],
      },
    }))
    await page.goto('/variants/rsYELIZTLI2464?sample_id=1')
    await waitForReactHydration(page)
    const overview = page.getByTestId('tab-overview')
    await expect(overview.getByText('YELIZTLI2464', { exact: true })).toBeVisible()
    if (source) {
      const row = overview.getByText('Gene source', { exact: true }).locator('..')
      await expect(row).toHaveText(`Gene source${source === 'clinvar' ? 'ClinVar' : 'Ensembl VEP'}`)
    } else {
      await expect(overview.getByText('Gene source', { exact: true })).toHaveCount(0)
    }
    const transcript = overview.getByText('Transcript', { exact: true }).locator('..')
    await expect(transcript).toHaveText('Transcript—')
    await expect(overview.getByText('MANE Select', { exact: true })).toHaveCount(0)
    const accessibility = await new AxeBuilder({ page }).include('[data-testid="tab-overview"]').analyze()
    expect(accessibility.violations).toEqual([])
  })
}
