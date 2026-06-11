/** Cancer module API types (P3-18). */

/** A single P/LP variant in the cancer panel. */
export interface CancerVariant {
  rsid: string
  gene_symbol: string
  genotype: string | null
  zygosity: string | null
  clinvar_significance: string
  clinvar_accession: string | null
  clinvar_review_stars: number
  clinvar_conditions: string | null
  syndromes: string[]
  cancer_types: string[]
  inheritance: string
  evidence_level: number
  cross_links: string[]
  pmids: string[]
}

/** All cancer P/LP findings for a sample. */
export interface CancerVariantsListResponse {
  items: CancerVariant[]
  total: number
}

/** A single cancer PRS result. */
export interface CancerPRS {
  trait: string
  name: string
  /** False → no validated reference distribution, so percentile is withheld (issue #7). */
  calibrated: boolean
  percentile: number | null
  z_score: number | null
  bootstrap_ci_lower: number | null
  bootstrap_ci_upper: number | null
  bootstrap_iterations: number
  snps_used: number
  snps_total: number
  coverage_fraction: number
  is_sufficient: boolean
  source_ancestry: string
  source_study: string
  source_pmid: string
  sample_size: number
  ancestry_mismatch: boolean
  ancestry_warning_text: string | null
  evidence_level: number
  research_use_only: boolean
}

/** All cancer PRS results for a sample. */
export interface CancerPRSListResponse {
  items: CancerPRS[]
  total: number
  sufficient_count: number
  insufficient_traits: string[]
}

/** Cancer module disclaimer text (P3-17). */
export interface CancerDisclaimerResponse {
  title: string
  text: string
}
