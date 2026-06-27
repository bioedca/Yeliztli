/**
 * SW-F2 — the SpliceAI splice-prediction badge on the variant-detail page.
 *
 * The variant-detail response carries an optional `spliceai_badge` (present only
 * when the optional BYO spliceai.db is installed and the variant has a prediction
 * at/above the ingest threshold). When present, the Clinical tab shows a
 * context-only "Splice Prediction (SpliceAI)" section that is explicitly NOT ACMG
 * evidence. This mocks the detail endpoint and asserts the section renders.
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

const VARIANT_WITH_SPLICEAI = {
  rsid: 'rs397508328',
  chrom: '7',
  pos: 117559590,
  ref: 'G',
  alt: 'A',
  genotype: 'GA',
  zygosity: 'het',
  gene_symbol: 'CFTR',
  transcript_id: null,
  consequence: 'splice_region_variant',
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
  gnomad_af_global: 0.001,
  gnomad_homozygous_count: null,
  rare_flag: true,
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
  spliceai_badge: {
    ds_max: 0.91,
    tier: 'high_confidence',
    symbol: 'CFTR',
    top_mode: 'acceptor_loss',
    top_mode_label: 'Acceptor loss',
    top_delta_position: 3,
    ds_acceptor_gain: 0.02,
    ds_acceptor_loss: 0.91,
    ds_donor_gain: 0.0,
    ds_donor_loss: 0.05,
    acmg_evidence: false,
    context_only: true,
    note: 'context only',
    pmid_citations: ['30661751'],
  },
}

test.describe('SpliceAI badge on variant detail (SW-F2)', () => {
  test('renders the splice-prediction section in the Clinical tab', async ({ page }) => {
    await page.route('**/api/variants/rs397508328**', (route) =>
      route.fulfill(jsonRoute(VARIANT_WITH_SPLICEAI)),
    )

    await page.goto('/variants/rs397508328?sample_id=1')
    await waitForReactHydration(page)

    await page.getByRole('tab', { name: /clinical/i }).click()

    await expect(page.getByText('SpliceAI splice prediction')).toBeVisible()
    await expect(page.getByText(/High-confidence/)).toBeVisible()
    await expect(page.getByText('Acceptor loss')).toBeVisible()
    await expect(page.getByText('3 nt downstream')).toBeVisible()
    // The "not ACMG evidence" caveat travels with the badge.
    await expect(page.getByText(/not ACMG evidence/i)).toBeVisible()
  })
})
