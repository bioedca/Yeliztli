/**
 * Issue #1027 — ClinVar low-penetrance / risk-allele findings should render as
 * their own labeled cautionary tier, not as ordinary red high-penetrance P/LP.
 */

import { expect, test } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

test('Rare Variants previous findings labels low-penetrance/risk-allele tier (#1027)', async ({
  page,
}) => {
  await page.route('**/api/analysis/rare-variants/findings**', (route) =>
    route.fulfill(
      jsonRoute({
        items: [
          {
            rsid: 'rs_low_penetrance',
            gene_symbol: 'BRCA1',
            category: 'clinvar_low_penetrance_or_risk_allele',
            evidence_level: 2,
            finding_text:
              'BRCA1 rs_low_penetrance — Pathogenic/Established risk allele; ClinVar marks this as lower-penetrance/risk-allele.',
            zygosity: 'het',
            clinvar_significance: 'Pathogenic/Established risk allele',
            clinvar_low_penetrance_or_risk_allele: true,
            conditions: 'Hereditary breast cancer',
            detail: { clinvar_low_penetrance_or_risk_allele: true },
          },
        ],
        total: 1,
      }),
    ),
  )

  await page.goto('/rare-variants?sample_id=1')
  await waitForReactHydration(page)

  const row = page.getByTestId('finding-row').filter({ hasText: 'BRCA1' })
  await expect(row).toBeVisible()
  await expect(
    row.getByRole('cell', { name: 'Pathogenic/Established risk allele', exact: true }),
  ).toBeVisible()

  const categoryPill = row.getByText('Low-penetrance / risk allele')
  await expect(categoryPill).toBeVisible()
  await expect(categoryPill).toHaveClass(/bg-amber-100/)
  await expect(categoryPill).not.toHaveClass(/bg-red-/)

  await expect(page.getByText('clinvar low penetrance or risk allele')).toHaveCount(0)
})
