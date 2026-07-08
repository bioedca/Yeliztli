/**
 * Issue #1704 - ensemble pathogenic UI must match the k-of-present backend rule.
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

const TWO_OF_TWO_ENSEMBLE_VARIANT = {
  rsid: 'rs1704',
  chrom: '1',
  pos: 1704,
  ref: 'A',
  alt: 'G',
  genotype: 'AG',
  zygosity: 'het',
  gene_symbol: 'GENE1704',
  transcript_id: null,
  consequence: 'missense_variant',
  hgvs_coding: null,
  hgvs_protein: null,
  strand: '+',
  exon_number: null,
  intron_number: null,
  mane_select: null,
  clinvar_significance: 'Uncertain significance',
  clinvar_review_stars: 1,
  clinvar_accession: null,
  clinvar_conditions: null,
  gnomad_af_global: 0.0001,
  gnomad_af_afr: null,
  gnomad_af_amr: null,
  gnomad_af_asj: null,
  gnomad_af_eas: null,
  gnomad_af_eur: null,
  gnomad_af_fin: null,
  gnomad_af_sas: null,
  gnomad_homozygous_count: 0,
  rare_flag: true,
  ultra_rare_flag: false,
  cadd_phred: 28.4,
  sift_score: null,
  sift_pred: null,
  polyphen2_hsvar_score: null,
  polyphen2_hsvar_pred: null,
  revel: 0.82,
  mutpred2: null,
  vest4: null,
  metasvm: null,
  metalr: null,
  gerp_rs: null,
  phylop: null,
  mpc: null,
  primateai: null,
  alphamissense_pathogenicity: null,
  alphamissense_class: null,
  alphamissense_badge: null,
  gtex_eqtl_badge: null,
  spliceai_badge: null,
  dbsnp_build: null,
  dbsnp_rsid_current: null,
  dbsnp_validation: null,
  disease_name: null,
  disease_id: null,
  phenotype_source: null,
  hpo_terms: null,
  inheritance_pattern: null,
  deleterious_count: 2,
  deleterious_total_assessed: 2,
  evidence_conflict: false,
  ensemble_pathogenic: true,
  annotation_coverage: 0,
  chrom_grch38: null,
  pos_grch38: null,
  transcripts: [],
  gene_phenotypes: [],
  evidence_conflict_detail: null,
}

test('variant detail labels a 2-of-2 ensemble hit by assessed axes', async ({ page }) => {
  await page.route('**/api/variants/rs1704**', (route) =>
    route.fulfill(jsonRoute(TWO_OF_TWO_ENSEMBLE_VARIANT)),
  )

  await page.goto('/variants/rs1704?sample_id=1')
  await waitForReactHydration(page)

  const correctedLabel =
    'Ensemble pathogenic: strict majority of assessed independent axes deleterious (2/2)'
  await expect(page.getByText(correctedLabel)).toBeVisible()
  await expect(page.getByText(/3 independent axes deleterious/)).toHaveCount(0)

  await page.getByRole('tab', { name: /clinical/i }).click()
  await expect(page.getByText(correctedLabel)).toBeVisible()
  await expect(page.getByText(/3 independent axes deleterious/)).toHaveCount(0)
})
