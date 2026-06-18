/**
 * SW-F3 — the GTEx eQTL regulatory-context badge on the variant-detail page.
 *
 * The variant-detail response carries an optional `gtex_eqtl_badge` (present only
 * when the optional gtex_eqtl.db is installed and the variant has an eQTL
 * association). When present, the Clinical tab shows a context-only "Regulatory
 * Context (GTEx eQTL)" section that is explicitly NOT ACMG evidence. This mocks
 * the detail endpoint and asserts the section renders.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const VARIANT_WITH_EQTL = {
  rsid: 'rs3131972',
  chrom: '1',
  pos: 752721,
  ref: 'A',
  alt: 'G',
  genotype: 'AG',
  zygosity: 'het',
  gene_symbol: 'RP11-206L10.10',
  transcript_id: null,
  consequence: 'intron_variant',
  hgvs_coding: null,
  hgvs_protein: null,
  strand: '+',
  exon_number: null,
  intron_number: null,
  mane_select: null,
  clinvar_significance: null,
  clinvar_review_stars: null,
  clinvar_accession: null,
  clinvar_conditions: null,
  gnomad_af_global: 0.4,
  gnomad_homozygous_count: null,
  rare_flag: false,
  ultra_rare_flag: false,
  cadd_phred: null,
  sift_score: null,
  sift_pred: null,
  polyphen2_hsvar_score: null,
  polyphen2_hsvar_pred: null,
  revel: null,
  mutpred2: null,
  vest4: null,
  metasvm: null,
  metalr: null,
  gerp_rs: null,
  phylop: null,
  mpc: null,
  primateai: null,
  dbsnp_build: null,
  dbsnp_rsid_current: null,
  dbsnp_validation: null,
  disease_name: null,
  disease_id: null,
  phenotype_source: null,
  hpo_terms: null,
  inheritance_pattern: null,
  deleterious_count: null,
  evidence_conflict: false,
  ensemble_pathogenic: false,
  annotation_coverage: 0,
  transcripts: [],
  gene_phenotypes: [],
  evidence_conflict_detail: null,
  gtex_eqtl_badge: {
    rsid: 'rs3131972',
    gene_ids: ['ENSG00000228327'],
    tissues: ['Whole_Blood', 'Lung'],
    n_associations: 2,
    top_gene_id: 'ENSG00000228327',
    top_tissue: 'Whole_Blood',
    top_pval_nominal: 2.4e-11,
    acmg_evidence: false,
    context_only: true,
    note: 'context only',
    pmid_citations: ['32913098'],
  },
}

test.describe('GTEx eQTL badge on variant detail (SW-F3)', () => {
  test('renders the regulatory-context section in the Clinical tab', async ({ page }) => {
    await page.route('**/api/variants/rs3131972**', (route) =>
      route.fulfill(jsonRoute(VARIANT_WITH_EQTL)),
    )

    await page.goto('/variants/rs3131972?sample_id=1')
    await waitForReactHydration(page)

    await page.getByRole('tab', { name: /clinical/i }).click()

    await expect(page.getByText('GTEx eQTL regulatory context')).toBeVisible()
    await expect(page.getByText('ENSG00000228327')).toBeVisible()
    await expect(page.getByText('Whole Blood')).toBeVisible() // underscores humanized
    // The "not ACMG evidence" caveat travels with the badge.
    await expect(page.getByText(/not ACMG evidence/i)).toBeVisible()
  })
})
