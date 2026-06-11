/** Traits & Personality API types (P3-64). */

/** Categorical level for a traits pathway. */
export type PathwayLevel = "Elevated" | "Moderate" | "Standard"

/** Per-SNP result within a traits pathway. */
export interface SNPDetail {
  rsid: string
  gene: string
  variant_name: string
  genotype: string | null
  category: PathwayLevel
  effect_summary: string
  evidence_level: number
  trait_domain: string | null
  recommendation: string | null
  pmids: string[]
  coverage_note: string | null
  cross_module: {
    to_module: string
    link_type: string
  } | null
}

/** Summary of a single traits pathway. */
export interface PathwaySummary {
  pathway_id: string
  pathway_name: string
  level: PathwayLevel
  evidence_level: number
  prs_primary: boolean
  called_snps: number
  total_snps: number
  missing_snps: string[]
  pmids: string[]
}

/** Cross-module link finding. */
export interface CrossModuleItem {
  rsid: string
  gene: string
  from_trait: string
  to_module: string
  link_type: string
  finding_text: string
  evidence_level: number
  pmids: string[]
}

/** All pathway results for a sample. */
export interface PathwaysResponse {
  items: PathwaySummary[]
  total: number
  cross_module: CrossModuleItem[]
  module_disclaimer: string
}

/** Full pathway detail with per-SNP breakdown. */
export interface PathwayDetailResponse {
  pathway_id: string
  pathway_name: string
  level: PathwayLevel
  evidence_level: number
  prs_primary: boolean
  called_snps: number
  total_snps: number
  missing_snps: string[]
  pmids: string[]
  snp_details: SNPDetail[]
}

/** A single PRS finding for traits. */
export interface TraitsPRS {
  trait: string
  name: string
  percentile: number | null
  z_score: number | null
  bootstrap_ci_lower: number | null
  bootstrap_ci_upper: number | null
  source_ancestry: string
  source_study: string
  snps_used: number
  snps_total: number
  coverage_fraction: number
  ancestry_mismatch: boolean
  ancestry_warning_text: string | null
  is_sufficient: boolean
  calibrated: boolean
  research_use_only: boolean
  evidence_level: number
}

/** All PRS results for the traits module. */
export interface PRSResponse {
  items: TraitsPRS[]
  total: number
  module_disclaimer: string
}

/** Module disclaimer. */
export interface DisclaimerResponse {
  disclaimer: string
  evidence_cap: number
  research_use_only: boolean
}
