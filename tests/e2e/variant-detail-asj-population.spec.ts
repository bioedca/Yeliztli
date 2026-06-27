/**
 * Issue #1092 — the variant-detail population tab includes gnomAD ASJ frequency.
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

const GBA_N370S_WITH_ASJ = {
  rsid: 'rs76763715',
  chrom: '1',
  pos: 155205634,
  ref: 'T',
  alt: 'C',
  genotype: 'TC',
  zygosity: 'het',
  gene_symbol: 'GBA',
  transcript_id: null,
  consequence: 'missense_variant',
  hgvs_coding: 'c.1226A>G',
  hgvs_protein: 'p.Asn409Ser',
  strand: '+',
  exon_number: null,
  intron_number: null,
  mane_select: null,
  clinvar_significance: 'Pathogenic',
  clinvar_review_stars: 3,
  clinvar_accession: 'VCV000004293',
  clinvar_conditions: 'Gaucher disease',
  gnomad_af_global: 0.002310653664434228,
  gnomad_af_afr: 0.00005124807819637764,
  gnomad_af_amr: 0.0004859086491739553,
  gnomad_af_asj: 0.026884920634920637,
  gnomad_af_eas: 0,
  gnomad_af_eur: 0.002075053634860901,
  gnomad_af_fin: 0.0012011457082139888,
  gnomad_af_sas: 0.0008278145695364238,
  gnomad_af_popmax: 0.026884920634920637,
  gnomad_homozygous_count: 0,
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
  alphamissense_pathogenicity: null,
  alphamissense_class: null,
  alphamissense_badge: null,
  gtex_eqtl_badge: null,
  spliceai_badge: null,
  dbsnp_build: null,
  dbsnp_rsid_current: null,
  dbsnp_validation: null,
  disease_name: 'Gaucher disease',
  disease_id: 'MONDO:0018150',
  phenotype_source: 'mondo_hpo',
  hpo_terms: null,
  inheritance_pattern: 'autosomal recessive',
  deleterious_count: null,
  deleterious_total_assessed: null,
  evidence_conflict: false,
  ensemble_pathogenic: false,
  annotation_coverage: 0,
  chrom_grch38: null,
  pos_grch38: null,
  transcripts: [],
  gene_phenotypes: [],
  evidence_conflict_detail: null,
}

test.describe('Variant detail ASJ population frequency', () => {
  test('renders Ashkenazi Jewish gnomAD AF in the population tab', async ({ page }) => {
    await page.route('**/api/variants/rs76763715**', (route) =>
      route.fulfill(jsonRoute(GBA_N370S_WITH_ASJ)),
    )

    await page.goto('/variants/rs76763715?sample_id=1')
    await waitForReactHydration(page)

    await page.getByRole('tab', { name: /population/i }).click()

    const asjRow = page.getByTestId('pop-bar-asj')
    await expect(asjRow).toBeVisible()
    await expect(asjRow).toContainText('Ashkenazi Jewish')
    await expect(asjRow).toContainText('0.0269')
  })
})
