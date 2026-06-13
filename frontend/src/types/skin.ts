/** Gene Skin API types (P3-56). */

import type { SnpCategory } from "@/lib/snpCategory"

/** Categorical level for a skin pathway. */
export type PathwayLevel = "Elevated" | "Moderate" | "Standard"

/** Per-SNP result within a skin pathway. */
export interface SNPDetail {
  rsid: string
  gene: string
  variant_name: string
  genotype: string | null
  category: SnpCategory
  effect_summary: string
  evidence_level: number
  recommendation: string | null
  pmids: string[]
  /** MC1R allele class (R = strong loss-of-function, r = mild). */
  mc1r_allele_class: string | null
  /** Coverage caveat (e.g. FLG proxy limitation). */
  coverage_note: string | null
  /** True if flagged as insufficient data (e.g. FLG 2282del4). */
  insufficient_data_flag: boolean
}

/** Summary of a single skin pathway. */
export interface PathwaySummary {
  pathway_id: string
  pathway_name: string
  level: PathwayLevel
  evidence_level: number
  called_snps: number
  total_snps: number
  missing_snps: string[]
  pmids: string[]
}

/** MC1R multi-allele aggregate result. */
export interface MC1RAggregateItem {
  r_allele_count: number
  r_allele_rsids: string[]
  total_mc1r_called: number
  risk_label: string
  risk_description: string
  evidence_level: number
  pmids: string[]
}

/** Cross-module reference finding. */
export interface CrossModuleItem {
  rsid: string
  gene: string
  source_module: string
  target_module: string
  finding_text: string
  evidence_level: number
  pmids: string[]
}

/** Insufficient data caveat (e.g. FLG 2282del4 proxy). */
export interface InsufficientDataItem {
  rsid: string
  gene: string
  finding_text: string
  evidence_level: number
  pathway: string
  proxy_target: string | null
  reason: string | null
  pmids: string[]
}

/** All pathway results for a sample. */
export interface PathwaysResponse {
  items: PathwaySummary[]
  total: number
  mc1r_aggregate: MC1RAggregateItem | null
  cross_module: CrossModuleItem[]
  insufficient_data: InsufficientDataItem[]
}

/** Full pathway detail with per-SNP breakdown. */
export interface PathwayDetailResponse {
  pathway_id: string
  pathway_name: string
  level: PathwayLevel
  evidence_level: number
  called_snps: number
  total_snps: number
  missing_snps: string[]
  pmids: string[]
  snp_details: SNPDetail[]
}
