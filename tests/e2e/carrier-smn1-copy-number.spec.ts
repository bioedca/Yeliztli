/**
 * Issue #1257 — SMN1 carrier status from SNP-array data must disclose that the
 * main SMA carrier-screening mechanism is copy-number/dosage, not ordinary
 * intragenic SNP presence/absence.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const SMN1_COPY_NUMBER_CAVEAT =
  'Copy-number not assessed: SNP-array data do not measure SMN1 exon 7 dosage/copy-number. Confirm SMN1 status with clinical testing that includes dosage/CNV assessment, such as qPCR or MLPA.'

const CARRIER_DISCLAIMER = {
  title: 'About carrier status',
  text: 'Reproductive carrier screening reference text.',
  gene_notes: {
    SMN1:
      'Consumer genotyping chips cannot reliably detect SMN1 copy number variations, which are the most common cause of SMA.',
  },
}

const SMN1_VARIANTS = {
  total: 1,
  genes_with_findings: ['SMN1'],
  items: [
    {
      rsid: 'rs121909192',
      gene_symbol: 'SMN1',
      genotype: 'A/G',
      zygosity: 'het',
      clinvar_significance: 'Pathogenic',
      clinvar_accession: 'VCV000012345',
      clinvar_review_stars: 2,
      clinvar_conditions: 'Spinal muscular atrophy',
      conditions: ['Spinal Muscular Atrophy'],
      inheritance: 'AR',
      evidence_level: 4,
      cross_links: [],
      pmids: ['35289093', '21673580'],
      notes: 'Point mutations account for a minority of pathogenic SMN1 alleles.',
      copy_number_limited: true,
      copy_number_caveat: SMN1_COPY_NUMBER_CAVEAT,
    },
  ],
}

test.describe('Carrier Status SMN1 copy-number disclosure (#1257)', () => {
  test('SMN1 point-mutation finding carries card and detail dosage caveats', async ({ page }) => {
    await page.route('**/api/analysis/carrier/variants**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(SMN1_VARIANTS),
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

    await expect(page.getByTestId('smn1-copy-number-notice')).toContainText(
      /SMN1 copy-number is not assessed/i,
    )

    const smn1Card = page.getByTestId('carrier-variant-card').filter({ hasText: 'SMN1' })
    await expect(smn1Card).toBeVisible()
    const cardDescribedBy = await smn1Card.getAttribute('aria-describedby')
    expect(cardDescribedBy).toBeTruthy()
    await expect(page.locator(`[id="${cardDescribedBy}"]`)).toContainText(/dosage\/CNV assessment/i)
    await expect(smn1Card.getByTestId('carrier-copy-number-caveat')).toContainText(
      /SMN1 exon 7 dosage\/copy-number/i,
    )

    await smn1Card.click()
    const detailPanel = page.getByTestId('carrier-detail-panel')
    await expect(detailPanel).toBeVisible()
    const panelDescribedBy = await detailPanel.getAttribute('aria-describedby')
    expect(panelDescribedBy).toBeTruthy()
    await expect(page.locator(`[id="${panelDescribedBy}"]`)).toContainText(
      /dosage\/CNV assessment/i,
    )
    await expect(detailPanel.getByTestId('carrier-copy-number-caveat-panel')).toContainText(
      /dosage\/CNV assessment/i,
    )
    await expect(detailPanel.getByText(/heterozygous carrier - typically unaffected/i)).toHaveCount(0)
  })

  test('no-finding state still warns that SMN1 absence is not carrier clearance', async ({ page }) => {
    await page.route('**/api/analysis/carrier/variants**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ total: 0, genes_with_findings: [], items: [] }),
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

    await expect(
      page.getByText('No carrier variants identified in the 7-gene panel for this sample.'),
    ).toBeVisible()
    await expect(page.getByTestId('smn1-copy-number-notice')).toContainText(
      /does not rule out SMA carrier status/i,
    )
    await expect(page.getByText(/SMN1 clear/i)).toHaveCount(0)
  })
})
