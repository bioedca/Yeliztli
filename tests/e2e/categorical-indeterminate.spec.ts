/**
 * Issue #369 — the categorical module detail panels (fitness, gene_health,
 * methylation, nutrigenomics, skin, traits) must render a per-SNP
 * `Indeterminate` category (a strand-ambiguous palindromic homozygote whose
 * call is withheld — #170/#269) as a first-class NEUTRAL (slate) category, not
 * fall back to the green "Standard" colour, and must surface the strand caveat.
 *
 * Both endpoints are stubbed (the views read `sample_id` from the URL): the
 * pathways list so a card renders, and the pathway detail so the slide-in panel
 * shows an Indeterminate SNP. We assert the badge uses the shared slate class
 * and the strand caveat (the SNP `effect_summary`) is visible.
 */

import { test, expect } from '@playwright/test'
import { waitForReactHydration } from './helpers'

function jsonRoute(payload: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(payload) }
}

const CAVEAT =
  'TT is a palindromic (A/T or C/G) homozygote: its strand — and therefore its effect ' +
  'category — cannot be determined from the array, so the result is withheld.'

// Superset of the per-module PathwaysResponse fields (gene_health ignores the
// skin-specific ones; skin's view dereferences mc1r_aggregate/insufficient_data).
function pathwaysPayload(pathwayId: string, pathwayName: string) {
  return {
    items: [
      {
        pathway_id: pathwayId,
        pathway_name: pathwayName,
        level: 'Standard',
        evidence_level: 1,
        called_snps: 1,
        total_snps: 1,
        missing_snps: [],
        pmids: [],
      },
    ],
    total: 1,
    cross_module: [],
    cross_context: [],
    module_disclaimer: null,
    mc1r_aggregate: null,
    insufficient_data: [],
    compound_het: [],
  }
}

function detailPayload(pathwayId: string, pathwayName: string) {
  return {
    pathway_id: pathwayId,
    pathway_name: pathwayName,
    level: 'Standard',
    evidence_level: 1,
    called_snps: 1,
    total_snps: 1,
    missing_snps: [],
    pmids: [],
    snp_details: [
      {
        rsid: 'rs9939609',
        gene: 'FTO',
        variant_name: 'rs9939609',
        genotype: 'TT',
        category: 'Indeterminate',
        effect_summary: CAVEAT,
        evidence_level: 1,
        recommendation: null,
        pmids: [],
        coverage_note: null,
        cross_module: null,
        three_state_label: null,
        insufficient_data_flag: false,
        mc1r_allele_class: null,
      },
    ],
  }
}

const MODULES = [
  { key: 'gene_health', path: '/gene-health', pathwayId: 'metabolic_health', pathwayName: 'Metabolic Health' },
  { key: 'skin', path: '/skin', pathwayId: 'sun_sensitivity', pathwayName: 'Sun Sensitivity' },
  { key: 'fitness', path: '/fitness', pathwayId: 'power', pathwayName: 'Power' },
  { key: 'methylation', path: '/methylation', pathwayId: 'folate_cycle', pathwayName: 'Folate Cycle' },
  { key: 'nutrigenomics', path: '/nutrigenomics', pathwayId: 'caffeine', pathwayName: 'Caffeine Metabolism' },
  { key: 'traits', path: '/traits', pathwayId: 'cognition', pathwayName: 'Cognition' },
]

for (const m of MODULES) {
  test.describe(`${m.key} detail panel renders Indeterminate as neutral (#369)`, () => {
    test('shows a slate Indeterminate badge + strand caveat, not green Standard', async ({
      page,
    }) => {
      await page.route(`**/api/analysis/${m.key}/pathways**`, async (route) => {
        await route.fulfill(jsonRoute(pathwaysPayload(m.pathwayId, m.pathwayName)))
      })
      await page.route(`**/api/analysis/${m.key}/pathway/**`, async (route) => {
        await route.fulfill(jsonRoute(detailPayload(m.pathwayId, m.pathwayName)))
      })

      await page.goto(`${m.path}?sample_id=1`)
      await waitForReactHydration(page)

      // Open the detail panel by clicking the pathway card.
      await page.getByRole('button', { name: new RegExp(m.pathwayName) }).first().click()

      const panel = page.getByRole('dialog', { name: new RegExp(`${m.pathwayName} pathway details`) })
      await expect(panel).toBeVisible()

      // Some panels (methylation) collapse the per-SNP breakdown behind an
      // "Advanced View" toggle — expand it if present.
      const advanced = panel.getByRole('button', { name: /Advanced View/i })
      if (await advanced.count()) {
        await advanced.first().click()
      }

      // The per-SNP category badge reads "Indeterminate" and uses the shared
      // neutral slate colour — NOT the emerald "Standard" fallback.
      const badge = panel.getByText('Indeterminate', { exact: true })
      await expect(badge).toBeVisible()
      await expect(badge).toHaveClass(/text-slate-600/)
      await expect(badge).not.toHaveClass(/text-emerald/)

      // The strand caveat (effect_summary) is surfaced so the user understands why.
      await expect(panel.getByText(/palindromic/i)).toBeVisible()
      await expect(panel.getByText(/cannot be determined from the array/i)).toBeVisible()
    })
  })
}
