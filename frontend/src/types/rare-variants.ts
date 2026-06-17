/** Rare variant finder API types (P3-30). */

/** Filter parameters for rare variant search. */
export interface RareVariantFilterRequest {
  gene_symbols: string[] | null
  af_threshold: number
  consequences: string[] | null
  clinvar_significance: string[] | null
  include_novel: boolean
  zygosity: string | null
}

/** A single rare variant in search results. */
export interface RareVariant {
  rsid: string
  chrom: string
  pos: number
  ref: string | null
  alt: string | null
  genotype: string | null
  zygosity: string | null
  gene_symbol: string | null
  consequence: string | null
  hgvs_coding: string | null
  hgvs_protein: string | null
  gnomad_af_global: number | null
  gnomad_af_afr: number | null
  gnomad_af_amr: number | null
  gnomad_af_eas: number | null
  gnomad_af_eur: number | null
  gnomad_af_fin: number | null
  gnomad_af_sas: number | null
  // True only when absent from gnomAD AND uncatalogued (#866). gnomAD-absence alone
  // is not novelty — render "Novel" from this, not from gnomad_af_global == null.
  is_novel: boolean
  clinvar_significance: string | null
  clinvar_low_penetrance_or_risk_allele?: boolean
  clinvar_review_stars: number | null
  clinvar_accession: string | null
  clinvar_conditions: string | null
  cadd_phred: number | null
  revel: number | null
  ensemble_pathogenic: boolean
  evidence_conflict: boolean
  evidence_level: number
  disease_name: string | null
  inheritance_pattern: string | null
}

/** Response from the search endpoint. */
export interface RareVariantSearchResponse {
  items: RareVariant[]
  total: number
  total_variants_scanned: number
  novel_count: number
  pathogenic_count: number
  genes_with_findings: string[]
  filters_applied: RareVariantFilterRequest
}

/** A stored finding from the findings table. */
interface RareVariantFinding {
  rsid: string | null
  gene_symbol: string | null
  category: string
  evidence_level: number
  finding_text: string
  zygosity: string | null
  clinvar_significance: string | null
  clinvar_low_penetrance_or_risk_allele?: boolean
  conditions: string | null
  detail: Record<string, unknown>
}

/** All stored rare variant findings for a sample. */
export interface RareVariantFindingsListResponse {
  items: RareVariantFinding[]
  total: number
}
