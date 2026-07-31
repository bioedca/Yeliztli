/**
 * Issue #2015 — Variant Detail exposes the gene literature that already ships
 * through Gene Detail instead of leaving a stale Phase 3 placeholder.
 */

import { expect, test } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

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
  transcript_id: 'NM_000546',
  consequence: 'missense_variant',
  hgvs_coding: 'c.215C>G',
  hgvs_protein: 'p.Pro72Arg',
  strand: '+',
  exon_number: 4,
  intron_number: null,
  mane_select: true,
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
  gene_phenotypes: [],
  evidence_conflict_detail: null,
}

const GENE_DETAIL = {
  gene_symbol: 'TP53',
  uniprot: null,
  uniprot_error: null,
  phenotypes: [],
  literature: [
    {
      pmid: '12345678',
      title: 'Synthetic TP53 literature title',
      abstract: 'A synthetic cached abstract for the browser regression.',
      authors: ['Ada Author', 'Ben Biologist'],
      journal: 'Synthetic Genetics',
      year: 2026,
      is_stale: false,
    },
  ],
  literature_errors: [],
  variants: [],
  population_af: [],
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await page.route('**/api/variants/rs1042522**', (route) =>
    route.fulfill({ json: VARIANT_DETAIL }),
  )
  await page.route('**/api/genes/TP53**', (route) =>
    route.fulfill({ json: GENE_DETAIL }),
  )
  await page.route('**/api/watches?sample_id=1', (route) => route.fulfill({ json: [] }))
})

test('loads gene literature, links its sources, and expands the abstract', async ({ page }) => {
  await page.goto('/variants/rs1042522?sample_id=1')
  await waitForReactHydration(page)

  await page.getByRole('tab', { name: 'Literature' }).click()

  await expect(page.getByRole('heading', { name: 'Literature (1)' })).toBeVisible()
  await expect(page.getByText('Synthetic TP53 literature title')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open PubMed 12345678' })).toHaveAttribute(
    'href',
    'https://pubmed.ncbi.nlm.nih.gov/12345678/',
  )
  await expect(
    page.getByRole('link', { name: 'View full gene detail for TP53' }),
  ).toHaveAttribute('href', '/genes/TP53?sample_id=1')
  await expect(page.getByText(/Phase 3/)).toHaveCount(0)

  const disclosure = page.getByRole('button', { name: /abstract/ })
  await expect(disclosure).toHaveText(/Show abstract/)
  await expect(disclosure).toHaveAttribute('aria-expanded', 'false')
  await disclosure.click()
  await expect(disclosure).toHaveText(/Hide abstract/)
  await expect(disclosure).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByText('A synthetic cached abstract for the browser regression.')).toBeVisible()
})
