/**
 * Issue #540 — the Carrier Status page applied recessive-"carrier" framing to its
 * autosomal-dominant genes (BRCA1/2). A heterozygous pathogenic BRCA2 variant is
 * not a silent recessive carrier — the heterozygote is themselves at elevated
 * personal (HBOC) risk — so calling them a "(heterozygous carrier)", language that
 * elsewhere on this page means "unaffected, risk only to offspring", risks false
 * reassurance in a consumer-health UI.
 *
 * The carrier-variants endpoint is stubbed with one AD gene (BRCA2) and one AR
 * gene (CFTR); the view reads `sample_id` from the URL and gates the cards only on
 * the variants query, so they render without genomic data. We assert the AD card
 * drops "carrier" (both the visible genotype line and the accessible name) while
 * the AR card keeps it, and that the page subtitle no longer claims the panel is
 * "autosomal recessive ... identification".
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const CARRIER_VARIANTS = {
  total: 2,
  genes_with_findings: ['BRCA2', 'CFTR'],
  items: [
    {
      // Autosomal dominant, cancer cross-linked (BRCA2 — HBOC personal risk).
      rsid: 'rs80359550',
      gene_symbol: 'BRCA2',
      genotype: 'CT/C',
      zygosity: 'het',
      clinvar_significance: 'Pathogenic',
      clinvar_accession: 'VCV000009325',
      clinvar_review_stars: 4,
      clinvar_conditions: 'Hereditary breast and ovarian cancer syndrome',
      conditions: ['Hereditary Breast and Ovarian Cancer Syndrome'],
      inheritance: 'AD',
      evidence_level: 4,
      cross_links: ['cancer'],
      pmids: ['20301425'],
      notes: 'Dual-role gene: cancer predisposition and reproductive carrier context.',
    },
    {
      // Autosomal recessive (CFTR — classic reproductive carrier).
      rsid: 'rs113993960',
      gene_symbol: 'CFTR',
      genotype: 'C/T',
      zygosity: 'het',
      clinvar_significance: 'Pathogenic',
      clinvar_accession: 'VCV000007105',
      clinvar_review_stars: 3,
      clinvar_conditions: 'Cystic fibrosis',
      conditions: ['Cystic Fibrosis'],
      inheritance: 'AR',
      evidence_level: 4,
      cross_links: [],
      pmids: ['20301428'],
      notes: 'Most common autosomal recessive condition in populations of European descent.',
    },
  ],
}

const CARRIER_DISCLAIMER = {
  title: 'About carrier status',
  text: 'Reproductive carrier screening reference text.',
  gene_notes: {},
}

test.describe('Carrier Status does not apply recessive-carrier framing to AD genes (#540)', () => {
  test('AD (BRCA2) card drops "carrier"; AR (CFTR) card keeps it; subtitle is accurate', async ({
    page,
  }) => {
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

    // Page subtitle no longer claims the (BRCA1/2-containing) panel is purely
    // "autosomal recessive ... identification".
    await expect(
      page.getByText(/Autosomal recessive carrier variant identification/i),
    ).toHaveCount(0)
    await expect(
      page.getByText(/Reproductive carrier screening, plus the autosomal-dominant genes BRCA1\/2/i),
    ).toBeVisible()

    const brca2Card = page.getByTestId('carrier-variant-card').filter({ hasText: 'BRCA2' })
    const cftrCard = page.getByTestId('carrier-variant-card').filter({ hasText: 'CFTR' })
    await expect(brca2Card).toBeVisible()
    await expect(cftrCard).toBeVisible()

    // AR gene (CFTR): classic recessive-carrier wording is preserved.
    await expect(cftrCard.getByText('(heterozygous carrier)')).toBeVisible()

    // AD gene (BRCA2): genotype line is annotated heterozygous but NOT "carrier",
    // and its footer still labels the gene Autosomal Dominant (no contradiction).
    await expect(brca2Card.getByText('(heterozygous)', { exact: true })).toBeVisible()
    await expect(brca2Card.getByText('(heterozygous carrier)')).toHaveCount(0)
    await expect(brca2Card.getByText('Autosomal Dominant')).toBeVisible()

    // The BRCA2 card's accessible name is also de-"carrier"-ed (screen readers).
    const brca2Label = await brca2Card.getAttribute('aria-label')
    expect(brca2Label).not.toMatch(/carrier/i)
    expect(brca2Label).toMatch(/heterozygous variant/i)

    // Clicking the AD card opens the slide-in detail panel — its accessible name
    // must also avoid "carrier" framing for a dominant-risk gene.
    await brca2Card.click()
    const detailPanel = page.getByTestId('carrier-detail-panel')
    await expect(detailPanel).toBeVisible()
    const detailLabel = await detailPanel.getAttribute('aria-label')
    expect(detailLabel).not.toMatch(/carrier/i)
    expect(detailLabel).toMatch(/BRCA2 variant detail/i)
  })
})
