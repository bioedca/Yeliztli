/**
 * Issue #1669 — HGVS c./p. labels need in-app notation explanations in
 * variant detail and rare-variant detail panels.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const hgvsCodingTooltip =
  'HGVS c. notation describes the coding-DNA change; > marks a single-base substitution.'
const hgvsProteinTooltip =
  'HGVS p. notation describes the predicted amino-acid change using three-letter amino-acid codes.'

const variantSummary = {
  items: [
    {
      rsid: 'rs1669',
      chrom: '17',
      pos: 43071077,
      genotype: 'AG',
      ref: 'A',
      alt: 'G',
      zygosity: 'het',
      gene_symbol: 'BRCA1',
      consequence: 'missense_variant',
      clinvar_significance: 'Pathogenic',
      clinvar_review_stars: 2,
      gnomad_af_global: 0.00023,
      rare_flag: true,
      cadd_phred: 28.5,
      sift_score: null,
      sift_pred: null,
      polyphen2_hsvar_score: null,
      polyphen2_hsvar_pred: null,
      revel: 0.892,
      annotation_coverage: 0b111111,
      evidence_conflict: false,
      ensemble_pathogenic: true,
      chrom_grch38: null,
      pos_grch38: null,
      tags: [],
      source: '',
      concordance: '',
    },
  ],
  next_cursor_chrom: null,
  next_cursor_pos: null,
  has_more: false,
  limit: 100,
}

const variantDetail = {
  rsid: 'rs1669',
  chrom: '17',
  pos: 43071077,
  ref: 'A',
  alt: 'G',
  genotype: 'AG',
  zygosity: 'het',
  gene_symbol: 'BRCA1',
  transcript_id: 'NM_007294',
  consequence: 'missense_variant',
  hgvs_coding: 'c.1234A>G',
  hgvs_protein: 'p.Asp412Gly',
  strand: '+',
  exon_number: 11,
  intron_number: null,
  mane_select: true,
  clinvar_significance: 'Pathogenic',
  clinvar_review_stars: 2,
  clinvar_accession: 'VCV000012345',
  clinvar_conditions: 'Breast-ovarian cancer, familial 1',
  gnomad_af_global: 0.00023,
  gnomad_af_afr: 0.0001,
  gnomad_af_amr: null,
  gnomad_af_eas: null,
  gnomad_af_eur: 0.0003,
  gnomad_af_fin: null,
  gnomad_af_sas: null,
  gnomad_homozygous_count: 0,
  rare_flag: true,
  ultra_rare_flag: false,
  cadd_phred: 28.5,
  sift_score: null,
  sift_pred: null,
  polyphen2_hsvar_score: null,
  polyphen2_hsvar_pred: null,
  revel: 0.892,
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
  disease_name: 'Breast cancer',
  disease_id: 'MONDO:0007254',
  phenotype_source: 'mondo_hpo',
  hpo_terms: null,
  inheritance_pattern: 'AD',
  deleterious_count: 3,
  evidence_conflict: false,
  ensemble_pathogenic: true,
  annotation_coverage: 0b111111,
  transcripts: [],
  gene_phenotypes: [],
  evidence_conflict_detail: null,
}

const rareVariant = {
  rsid: variantDetail.rsid,
  chrom: variantDetail.chrom,
  pos: variantDetail.pos,
  ref: variantDetail.ref,
  alt: variantDetail.alt,
  genotype: variantDetail.genotype,
  zygosity: variantDetail.zygosity,
  zygosity_label: 'Heterozygous',
  gene_symbol: variantDetail.gene_symbol,
  consequence: variantDetail.consequence,
  hgvs_coding: variantDetail.hgvs_coding,
  hgvs_protein: variantDetail.hgvs_protein,
  gnomad_af_global: variantDetail.gnomad_af_global,
  gnomad_af_afr: variantDetail.gnomad_af_afr,
  gnomad_af_amr: variantDetail.gnomad_af_amr,
  gnomad_af_asj: null,
  gnomad_af_eas: variantDetail.gnomad_af_eas,
  gnomad_af_eur: variantDetail.gnomad_af_eur,
  gnomad_af_fin: variantDetail.gnomad_af_fin,
  gnomad_af_sas: variantDetail.gnomad_af_sas,
  is_novel: false,
  clinvar_significance: variantDetail.clinvar_significance,
  clinvar_review_stars: variantDetail.clinvar_review_stars,
  clinvar_accession: variantDetail.clinvar_accession,
  clinvar_conditions: variantDetail.clinvar_conditions,
  cadd_phred: variantDetail.cadd_phred,
  revel: variantDetail.revel,
  ensemble_pathogenic: true,
  evidence_conflict: false,
  evidence_level: 4,
  disease_name: variantDetail.disease_name,
  inheritance_pattern: variantDetail.inheritance_pattern,
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

test('Variant Explorer side panel explains HGVS coding and protein notation (#1669)', async ({
  page,
}) => {
  await page.route(/\/api\/column-presets(\?|\/|$)/, (route) =>
    route.fulfill(jsonRoute({ presets: [] })),
  )
  await page.route(/\/api\/tags(\?|$)/, (route) => route.fulfill(jsonRoute([])))
  await page.route(/\/api\/variants\/count(\?|$)/, (route) =>
    route.fulfill(jsonRoute({ total: 1, filtered: false })),
  )
  await page.route(/\/api\/variants\/chromosomes(\?|$)/, (route) =>
    route.fulfill(jsonRoute([{ chrom: '17', count: 1 }])),
  )
  await page.route(/\/api\/variants\/rs1669(\?|$)/, (route) =>
    route.fulfill(jsonRoute(variantDetail)),
  )
  await page.route(/\/api\/variants(\?[^/]*)?$/, (route) =>
    route.fulfill(jsonRoute(variantSummary)),
  )
  await page.route(/\/api\/samples\/\d+\/merge-provenance$/, (route) =>
    route.fulfill(jsonRoute({ detail: 'Sample is not a merged sample' }, 404)),
  )
  await page.route(/\/api\/watches(\?|$)/, (route) => route.fulfill(jsonRoute([])))

  await page.goto('/variants?sample_id=1')
  await waitForReactHydration(page)
  await page.getByText('rs1669').click()

  await expect(page.getByTitle(hgvsCodingTooltip).filter({ hasText: /^Coding$/ })).toHaveText(
    'Coding',
  )
  await expect(page.getByTitle(hgvsProteinTooltip).filter({ hasText: /^Protein$/ })).toHaveText(
    'Protein',
  )
})

test('Full variant detail page explains HGVS coding and protein notation (#1669)', async ({
  page,
}) => {
  await page.route(/\/api\/variants\/rs1669(\?|$)/, (route) =>
    route.fulfill(jsonRoute(variantDetail)),
  )
  await page.route(/\/api\/watches(\?|$)/, (route) => route.fulfill(jsonRoute([])))

  await page.goto('/variants/rs1669?sample_id=1')
  await waitForReactHydration(page)

  await expect(page.getByTitle(hgvsCodingTooltip).filter({ hasText: /^Coding$/ })).toHaveText(
    'Coding',
  )
  await expect(page.getByTitle(hgvsProteinTooltip).filter({ hasText: /^Protein$/ })).toHaveText(
    'Protein',
  )

  await page.getByRole('tab', { name: /protein/i }).click()
  await expect(page.getByTitle(hgvsProteinTooltip)).toHaveText('Protein change')
})

test('Rare Variant Finder detail panel explains HGVS coding and protein notation (#1669)', async ({
  page,
}) => {
  await page.route('**/api/panels', (route) => route.fulfill(jsonRoute({ items: [] })))
  await page.route('**/api/analysis/rare-variants/findings**', (route) =>
    route.fulfill(jsonRoute({ items: [], total: 0 })),
  )
  await page.route('**/api/analysis/rare-variants/search**', (route) =>
    route.fulfill(
      jsonRoute({
        items: [rareVariant],
        total: 1,
        total_variants_scanned: 1,
        novel_count: 0,
        pathogenic_count: 1,
        genes_with_findings: ['BRCA1'],
        filters_applied: {
          gene_symbols: null,
          af_threshold: 0.01,
          consequences: null,
          clinvar_significance: null,
          include_novel: true,
          zygosity: null,
        },
      }),
    ),
  )

  await page.goto('/rare-variants?sample_id=1')
  await waitForReactHydration(page)
  await page.getByTestId('search-button').click()
  await page.getByTestId('result-row').filter({ hasText: 'BRCA1' }).click()

  await expect(page.getByTitle(hgvsCodingTooltip)).toHaveText('HGVS Coding')
  await expect(page.getByTitle(hgvsProteinTooltip)).toHaveText('HGVS Protein')
})
