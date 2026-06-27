/**
 * Issue #1121 - non-coding variants missed by the current gnomAD exome source
 * must render as source-uncovered, not as ordinary allele absence.
 */

import { expect, test } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const SOURCE_UNCOVERED_VARIANT = {
  rsid: 'rs1799963',
  chrom: '11',
  pos: 46761055,
  ref: 'G',
  alt: 'A',
  genotype: 'GA',
  zygosity: 'het',
  gene_symbol: 'F2',
  transcript_id: 'ENST00000311907',
  consequence: '3_prime_UTR_variant',
  hgvs_coding: null,
  hgvs_protein: null,
  strand: '+',
  exon_number: null,
  intron_number: null,
  mane_select: null,
  clinvar_significance: 'Pathogenic',
  clinvar_review_stars: 4,
  clinvar_accession: 'VCV000009999',
  clinvar_conditions: 'Thrombophilia due to thrombin defect',
  gnomad_af_global: null,
  gnomad_af_afr: null,
  gnomad_af_amr: null,
  gnomad_af_asj: null,
  gnomad_af_eas: null,
  gnomad_af_eur: null,
  gnomad_af_fin: null,
  gnomad_af_sas: null,
  gnomad_af_popmax: null,
  gnomad_source_status: 'source_uncovered',
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
  alphamissense_pathogenicity: null,
  alphamissense_class: null,
  alphamissense_badge: null,
  gtex_eqtl_badge: null,
  spliceai_badge: null,
  dbsnp_build: 156,
  dbsnp_rsid_current: 'rs1799963',
  dbsnp_validation: null,
  disease_name: 'Thrombophilia due to thrombin defect',
  disease_id: 'MONDO:0017777',
  phenotype_source: 'mondo_hpo',
  hpo_terms: null,
  inheritance_pattern: 'autosomal dominant',
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

const SOURCE_UNCOVERED_RARE_VARIANT = {
  rsid: SOURCE_UNCOVERED_VARIANT.rsid,
  chrom: SOURCE_UNCOVERED_VARIANT.chrom,
  pos: SOURCE_UNCOVERED_VARIANT.pos,
  ref: SOURCE_UNCOVERED_VARIANT.ref,
  alt: SOURCE_UNCOVERED_VARIANT.alt,
  genotype: SOURCE_UNCOVERED_VARIANT.genotype,
  zygosity: SOURCE_UNCOVERED_VARIANT.zygosity,
  gene_symbol: SOURCE_UNCOVERED_VARIANT.gene_symbol,
  consequence: SOURCE_UNCOVERED_VARIANT.consequence,
  hgvs_coding: SOURCE_UNCOVERED_VARIANT.hgvs_coding,
  hgvs_protein: SOURCE_UNCOVERED_VARIANT.hgvs_protein,
  gnomad_af_global: null,
  gnomad_af_afr: null,
  gnomad_af_amr: null,
  gnomad_af_asj: null,
  gnomad_af_eas: null,
  gnomad_af_eur: null,
  gnomad_af_fin: null,
  gnomad_af_sas: null,
  gnomad_source_status: 'source_uncovered',
  is_novel: false,
  clinvar_significance: SOURCE_UNCOVERED_VARIANT.clinvar_significance,
  clinvar_review_stars: SOURCE_UNCOVERED_VARIANT.clinvar_review_stars,
  clinvar_accession: SOURCE_UNCOVERED_VARIANT.clinvar_accession,
  clinvar_conditions: SOURCE_UNCOVERED_VARIANT.clinvar_conditions,
  cadd_phred: null,
  revel: null,
  ensemble_pathogenic: false,
  evidence_conflict: false,
  evidence_level: 4,
  disease_name: SOURCE_UNCOVERED_VARIANT.disease_name,
  inheritance_pattern: SOURCE_UNCOVERED_VARIANT.inheritance_pattern,
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

test('variant detail labels source-uncovered gnomAD frequency distinctly', async ({ page }) => {
  await page.route('**/api/variants/rs1799963**', (route) =>
    route.fulfill(jsonRoute(SOURCE_UNCOVERED_VARIANT)),
  )

  await page.goto('/variants/rs1799963?sample_id=1')
  await waitForReactHydration(page)

  await expect(page.getByText('Not assessed', { exact: true })).toBeVisible()

  await page.getByRole('tab', { name: /population/i }).click()
  await expect(
    page.getByText('Not assessed by current gnomAD exome source'),
  ).toBeVisible()
})

test('rare variant search labels source-uncovered gnomAD frequency distinctly', async ({
  page,
}) => {
  await page.route('**/api/panels', (route) => route.fulfill(jsonRoute({ items: [] })))
  await page.route('**/api/analysis/rare-variants/findings**', (route) =>
    route.fulfill(jsonRoute({ items: [], total: 0 })),
  )
  await page.route('**/api/analysis/rare-variants/search**', (route) =>
    route.fulfill(
      jsonRoute({
        items: [SOURCE_UNCOVERED_RARE_VARIANT],
        total: 1,
        total_variants_scanned: 1,
        novel_count: 0,
        pathogenic_count: 1,
        genes_with_findings: ['F2'],
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

  const row = page.getByTestId('result-row').filter({ hasText: 'F2' })
  await expect(row).toContainText('Not assessed by current gnomAD exome source')

  await row.click()
  await expect(
    page.getByText('Not assessed by current gnomAD exome source').last(),
  ).toBeVisible()
})
