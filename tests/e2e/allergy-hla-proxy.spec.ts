/**
 * Issue #402 — the allergy HLAProxyBadge previously read the singular
 * `snp.hla_proxy.r_squared`, which is undefined on the backend's `hla_proxy`
 * block, so `undefined.toFixed(2)` crashed for every HLA-proxy SNP (rs2395029,
 * rs144012689, rs1061235, rs9263726). No spec exercised the non-null path.
 *
 * This stubs the allergy pathways list + the drug-hypersensitivity detail
 * (AllergyView reads `sample_id` from the URL) and drives into the detail panel,
 * asserting the HLA proxy badge renders the per-population r² from
 * `hla_proxy_lookup` with no crash and no NaN.
 */

import { test, expect } from '@playwright/test'
import { waitForReactHydration } from './helpers'

function jsonRoute(payload: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(payload) }
}

const PATHWAYS = {
  total: 1,
  cross_module: [],
  celiac_combined: null,
  histamine_combined: null,
  items: [
    {
      pathway_id: 'drug_hypersensitivity',
      pathway_name: 'Drug Hypersensitivity',
      level: 'Moderate',
      evidence_level: 4,
      called_snps: 1,
      total_snps: 1,
      missing_snps: [],
      pmids: ['18192595'],
      hla_proxy_lookup: null,
    },
  ],
}

const DRUG_DETAIL = {
  pathway_id: 'drug_hypersensitivity',
  pathway_name: 'Drug Hypersensitivity',
  level: 'Moderate',
  evidence_level: 4,
  called_snps: 1,
  total_snps: 1,
  missing_snps: [],
  pmids: ['18192595'],
  hla_proxy_lookup: null,
  snp_details: [
    {
      rsid: 'rs2395029',
      gene: 'HLA-B',
      variant_name: 'HLA-B*57:01 proxy',
      genotype: 'TG',
      category: 'Moderate',
      effect_summary: 'HLA-B*57:01 tag-SNP positive',
      evidence_level: 4,
      recommendation: null,
      pmids: [],
      hla_proxy: { hla_allele: 'HLA-B*57:01', clinical_grade: true, confirmatory_test_required: true },
      hla_proxy_lookup: { hla_allele: 'HLA-B*57:01', r_squared_by_pop: { EUR: 0.97, AFR: 0.85 } },
      coverage_note: null,
    },
  ],
}

test.describe('Allergy HLA proxy badge (#402)', () => {
  test('drug-hypersensitivity HLA proxy SNP renders r² without crashing', async ({ page }) => {
    await page.route('**/api/analysis/allergy/pathways**', async (route) => {
      await route.fulfill(jsonRoute(PATHWAYS))
    })
    await page.route('**/api/analysis/allergy/pathway/drug_hypersensitivity**', async (route) => {
      await route.fulfill(jsonRoute(DRUG_DETAIL))
    })

    const consoleErrors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })

    await page.goto('/allergy?sample_id=1')
    await waitForReactHydration(page)

    // Open the drug-hypersensitivity pathway detail.
    await page.getByRole('button', { name: /Drug Hypersensitivity/ }).first().click()

    // The HLA proxy badge renders the allele + the conservative (min) r² from
    // the per-population lookup — and never a NaN.
    const badge = page.getByText(/HLA Proxy:/)
    await expect(badge).toBeVisible()
    await expect(badge.locator('xpath=..')).toContainText('HLA-B*57:01')
    await expect(badge.locator('xpath=..')).toContainText('min r²=0.85')
    await expect(badge.locator('xpath=..')).not.toContainText('NaN')

    // No render-time TypeError (undefined.toFixed) reached the console.
    expect(consoleErrors.join('\n')).not.toMatch(/toFixed|TypeError|NaN/)
  })
})
