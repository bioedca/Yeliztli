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
  /** Per-PGS provenance + monogenic exclusion (SW-B3). */
  pgs_id: string | null
  pgs_license: string | null
  development_method: string | null
  genome_build: string | null
  variants_number: number | null
  source_url: string | null
  monogenic_genes: string[]
  monogenic_carrier_genes: string[]
  monogenic_note: string | null
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

/** Breast absolute-risk overlay (SW-B8, opt-in). Shape varies by consent. */
interface AbsoluteRiskMonogenic {
  gene: string
  cumulative_risk_to_80_pct: number | null
  ci: string | null
  pmid: string | null
  note?: string
}

export interface AbsoluteRiskResponse {
  consented: boolean
  opt_in_required?: boolean
  opt_in_prompt?: string
  disclaimer: string
  /** Inferred biological sex (SW-B8 / gh #151). */
  inferred_sex?: "XX" | "XY" | "manual_review" | "unknown" | null
  /** Which sex context the figures reflect: "female" | "male" | "unresolved". */
  sex_context?: "female" | "male" | "unresolved"
  /** Plain-language note explaining the sex context and which figures apply / are withheld. */
  sex_note?: string
  /** Female SEER lifetime baseline — present only for the female context. */
  population_baseline?: {
    lifetime_risk_pct: number
    source: string
    source_url: string
    note: string
  }
  has_monogenic?: boolean
  monogenic?: AbsoluteRiskMonogenic[]
  prs_note?: string
  canrisk?: { tool: string; url: string; pmid: string; note: string }
}
