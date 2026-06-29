/**
 * Issue #1168 — autosomal-recessive biallelic patterns must not be presented as
 * ordinary "typically unaffected" carrier findings. The endpoint is stubbed with
 * one possible compound-heterozygote CFTR result and one homozygous CFTR result
 * so the page can be verified without real genomic data.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const CARRIER_VARIANTS = {
  total: 2,
  genes_with_findings: ['CFTR'],
  items: [
    {
      rsid: 'rs78655421, i4000299',
      gene_symbol: 'CFTR',
      genotype: 'rs78655421:AG; i4000299:CT',
      zygosity: 'possible_compound_heterozygous',
      clinvar_significance: 'Pathogenic',
      clinvar_accession: 'VCV000007105; VCV000007107',
      clinvar_review_stars: 2,
      clinvar_conditions: 'Cystic fibrosis',
      conditions: ['Cystic Fibrosis'],
      inheritance: 'AR',
      evidence_level: 4,
      cross_links: [],
      pmids: ['32454915'],
      notes: 'CFTR carrier-panel example.',
      finding_type: 'possible_compound_heterozygote',
      variant_ids: ['rs78655421', 'i4000299'],
      component_variants: [
        {
          rsid: 'rs78655421',
          chrom: '7',
          pos: 117171029,
          ref: 'A',
          alt: 'G',
          genotype: 'AG',
          zygosity: 'het',
          clinvar_significance: 'Pathogenic',
          clinvar_review_stars: 3,
          clinvar_accession: 'VCV000007105',
          clinvar_conditions: 'Cystic fibrosis',
        },
        {
          rsid: 'i4000299',
          chrom: '7',
          pos: 117199683,
          ref: 'C',
          alt: 'T',
          genotype: 'CT',
          zygosity: 'het',
          clinvar_significance: 'Likely pathogenic',
          clinvar_review_stars: 2,
          clinvar_accession: 'VCV000007107',
          clinvar_conditions: 'Cystic fibrosis',
        },
      ],
      phase_caveat:
        'Genotyping arrays do not phase these variants, so this result cannot distinguish in-trans affected status from same-chromosome variants.',
    },
    {
      rsid: 'rs75961395',
      gene_symbol: 'CFTR',
      genotype: 'TT',
      zygosity: 'hom_alt',
      clinvar_significance: 'Pathogenic',
      clinvar_accession: 'VCV000007106',
      clinvar_review_stars: 2,
      clinvar_conditions: 'Cystic fibrosis',
      conditions: ['Cystic Fibrosis'],
      inheritance: 'AR',
      evidence_level: 4,
      cross_links: [],
      pmids: ['32454915'],
      notes: 'CFTR carrier-panel example.',
      finding_type: 'affected_homozygous',
      variant_ids: ['rs75961395'],
      component_variants: [
        {
          rsid: 'rs75961395',
          chrom: '7',
          pos: 117559600,
          ref: null,
          alt: null,
          genotype: 'TT',
          zygosity: 'hom_alt',
          clinvar_significance: 'Pathogenic',
          clinvar_review_stars: 2,
          clinvar_accession: 'VCV000007106',
          clinvar_conditions: 'Cystic fibrosis',
        },
      ],
    },
  ],
}

const CARRIER_DISCLAIMER = {
  title: 'About carrier status',
  text: 'Reproductive carrier screening reference text.',
  gene_notes: {},
}

test.describe('Carrier Status affected-status findings (#1168)', () => {
  test('biallelic AR findings avoid ordinary carrier framing', async ({ page }) => {
    await page.route('**/api/analysis/carrier/variants**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(CARRIER_VARIANTS),
      })
    })
    await page.route('**/api/analysis/carrier/disclaimer**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(CARRIER_DISCLAIMER),
      })
    })

    await page.goto('/carrier-status?sample_id=1')
    await waitForReactHydration(page)

    await expect(page.getByText('Carrier and Affected-Status Findings')).toBeVisible()
    const summary = page.getByTestId('carrier-summary')
    const affectedSummary = summary.locator(':scope > div > div').filter({
      hasText: 'Affected-status:',
    })
    await expect(affectedSummary.getByText('Affected-status:')).toBeVisible()
    await expect(affectedSummary.locator('.font-semibold')).toHaveText('2')

    const compoundCard = page.getByTestId('carrier-variant-card').filter({ hasText: 'i4000299' })
    await expect(compoundCard).toBeVisible()
    await expect(compoundCard.getByText('(possible compound heterozygote)')).toBeVisible()
    await expect(compoundCard.getByText(/Possible affected-status pattern/i)).toBeVisible()
    await expect(compoundCard.getByText('(heterozygous carrier)')).toHaveCount(0)
    const compoundLabel = await compoundCard.getAttribute('aria-label')
    expect(compoundLabel).not.toMatch(/carrier/i)
    expect(compoundLabel).toMatch(/possible compound heterozygote affected-status/i)

    await compoundCard.click()
    const detailPanel = page.getByTestId('carrier-detail-panel')
    await expect(detailPanel).toBeVisible()
    expect(await detailPanel.getAttribute('aria-label')).toMatch(/affected-status finding detail/i)
    expect(await detailPanel.getAttribute('aria-label')).not.toMatch(/carrier/i)
    await expect(detailPanel.getByText(/if they are in trans/i)).toBeVisible()
    await expect(detailPanel.getByText(/do not phase these variants/i)).toBeVisible()
    const componentVariants = detailPanel.getByTestId('carrier-component-variants')
    await expect(componentVariants).toBeVisible()
    await expect(componentVariants.getByText('rs78655421', { exact: true })).toBeVisible()
    await expect(componentVariants.getByText('i4000299', { exact: true })).toBeVisible()

    await page.getByLabel('Close panel').click()

    const homozygousCard = page.getByTestId('carrier-variant-card').filter({ hasText: 'rs75961395' })
    await expect(homozygousCard).toBeVisible()
    await expect(homozygousCard.getByText('(homozygous affected-status)')).toBeVisible()
    await expect(homozygousCard.getByText(/Affected-status result/i)).toBeVisible()
    await expect(homozygousCard.getByText('(heterozygous carrier)')).toHaveCount(0)
    const homozygousLabel = await homozygousCard.getAttribute('aria-label')
    expect(homozygousLabel).not.toMatch(/carrier/i)
    expect(homozygousLabel).toMatch(/homozygous affected-status finding/i)
  })
})
