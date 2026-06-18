/** Types for variant detail API response (P2-20, P2-21). */

export interface TranscriptAnnotation {
  transcript_id: string | null
  gene_symbol: string | null
  consequence: string | null
  hgvs_coding: string | null
  hgvs_protein: string | null
  strand: string | null
  exon_number: number | null
  intron_number: number | null
  mane_select: boolean
}

export interface GenePhenotypeRecord {
  gene_symbol: string
  disease_name: string
  disease_id: string | null
  source: string
  hpo_terms: string[] | null
  inheritance: string | null
  omim_link: string | null
}

export interface EvidenceConflictDetail {
  has_conflict: boolean
  clinvar_significance: string | null
  clinvar_review_stars: number | null
  clinvar_accession: string | null
  deleterious_count: number | null
  total_tools_assessed: number
  deleterious_tools: string[]
  cadd_phred: number | null
  summary: string | null
}

export interface VariantDetail {
  // Core
  rsid: string
  chrom: string
  pos: number
  ref: string | null
  alt: string | null
  genotype: string | null
  zygosity: string | null

  // VEP (best transcript)
  gene_symbol: string | null
  transcript_id: string | null
  consequence: string | null
  hgvs_coding: string | null
  hgvs_protein: string | null
  strand: string | null
  exon_number: number | null
  intron_number: number | null
  mane_select: boolean | null

  // ClinVar
  clinvar_significance: string | null
  clinvar_review_stars: number | null
  clinvar_accession: string | null
  clinvar_conditions: string | null

  // gnomAD
  gnomad_af_global: number | null
  gnomad_af_afr: number | null
  gnomad_af_amr: number | null
  gnomad_af_eas: number | null
  gnomad_af_eur: number | null
  gnomad_af_fin: number | null
  gnomad_af_sas: number | null
  gnomad_homozygous_count: number | null
  rare_flag: boolean | null
  ultra_rare_flag: boolean | null

  // dbNSFP
  cadd_phred: number | null
  sift_score: number | null
  sift_pred: string | null
  polyphen2_hsvar_score: number | null
  polyphen2_hsvar_pred: string | null
  revel: number | null
  mutpred2: number | null
  vest4: number | null
  metasvm: number | null
  metalr: number | null
  gerp_rs: number | null
  phylop: number | null
  mpc: number | null
  primateai: number | null

  // dbSNP
  dbsnp_build: number | null
  dbsnp_rsid_current: string | null
  dbsnp_validation: string | null

  // Gene-phenotype
  disease_name: string | null
  disease_id: string | null
  phenotype_source: string | null
  hpo_terms: string | null
  inheritance_pattern: string | null

  // Ensemble / conflict
  deleterious_count: number | null
  evidence_conflict: boolean | null
  ensemble_pathogenic: boolean | null
  annotation_coverage: number | null

  // Extended detail (P2-20)
  transcripts: TranscriptAnnotation[]
  gene_phenotypes: GenePhenotypeRecord[]
  evidence_conflict_detail: EvidenceConflictDetail | null

  // GTEx eQTL regulatory context (SW-F3) — present only when gtex_eqtl.db is
  // installed and the variant has an eQTL association. Context-only; never ACMG.
  gtex_eqtl_badge?: GTExEqtlBadge | null
}

/** Context-only GTEx eQTL regulatory association summary (SW-F3).
 * Mirrors backend `analysis/gtex.eqtl_regulatory_context`. An eQTL is a
 * statistical association with gene expression — NOT a causal-mechanism claim
 * and NEVER ACMG evidence (`acmg_evidence` is always false). */
export interface GTExEqtlBadge {
  rsid: string
  gene_ids: string[]
  tissues: string[]
  n_associations: number
  top_gene_id: string | null
  top_tissue: string | null
  top_pval_nominal: number | null
  acmg_evidence: boolean
  context_only: boolean
  note: string | null
  pmid_citations: string[]
}
