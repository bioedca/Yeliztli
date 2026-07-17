/**
 * Issue #2004 — HPO labels survive ingestion/API transport and render label-first.
 */

import { expect, test } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const HPO_TERM_DETAILS = Array.from({ length: 10 }, (_, index) => ({
  id: `HP:${String(index + 1).padStart(7, '0')}`,
  name: `Phenotype ${index + 1}`,
}))
const HPO_TERM_IDS = HPO_TERM_DETAILS.map((term) => term.id)

const PHENOTYPE = {
  gene_symbol: 'TP53',
  disease_name: 'Li-Fraumeni syndrome',
  disease_id: 'MONDO:0018875',
  source: 'mondo_hpo',
  hpo_terms: HPO_TERM_IDS,
  hpo_term_details: HPO_TERM_DETAILS,
  inheritance: 'Autosomal dominant',
  omim_link: null,
}

const VARIANT_DETAIL = {
  rsid: 'rs1042522',
  chrom: '17',
  pos: 7676154,
  ref: 'C',
  alt: 'G',
  genotype: 'CG',
  zygosity: 'het',
  zygosity_label: 'Heterozygous',
  gene_symbol: 'TP53',
  transcript_id: null,
  consequence: 'missense_variant',
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
  gnomad_af_global: null,
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
  gene_phenotypes: [PHENOTYPE],
  evidence_conflict_detail: null,
}

test('labels and bounds HPO terms on variant detail', async ({ page }) => {
  await page.route('**/api/variants/rs1042522**', (route) =>
    route.fulfill({ json: VARIANT_DETAIL }),
  )

  await page.goto('/variants/rs1042522?sample_id=1')
  await waitForReactHydration(page)
  await page.getByRole('tab', { name: /clinical/i }).click()

  await expect(page.getByText('Phenotype 1 (HP:0000001)')).toBeVisible()
  await expect(page.getByText('Phenotype 9 (HP:0000009)')).toHaveCount(0)

  const disclosure = page.getByRole('button', { name: 'Show 2 more HPO terms' })
  await expect(disclosure).toHaveAttribute('aria-expanded', 'false')
  await disclosure.click()

  await expect(page.getByText('Phenotype 9 (HP:0000009)')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Show fewer HPO terms' })).toHaveAttribute(
    'aria-expanded',
    'true',
  )
})

test('labels and bounds HPO terms on gene detail', async ({ page }) => {
  await page.route('**/api/genes/TP53**', (route) =>
    route.fulfill({
      json: {
        gene_symbol: 'TP53',
        uniprot: null,
        uniprot_error: null,
        phenotypes: [PHENOTYPE],
        literature: [],
        literature_errors: [],
        variants: [],
        population_af: [],
      },
    }),
  )

  await page.goto('/genes/TP53?sample_id=1')
  await waitForReactHydration(page)

  await expect(page.getByText('Phenotype 1 (HP:0000001)')).toBeVisible()
  await expect(page.getByText('Phenotype 9 (HP:0000009)')).toHaveCount(0)

  await page.getByRole('button', { name: 'Show 2 more HPO terms' }).click()
  await expect(page.getByText('Phenotype 9 (HP:0000009)')).toBeVisible()
})
