/** Gene Health API types (P3-66). */

import type { SnpCategory } from "@/lib/snpCategory"

/** Categorical level for a gene-health pathway. */
type PathwayLevel = "Elevated" | "Moderate" | "Standard"

/** Per-SNP result within a gene-health pathway. */
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
  coverage_note: string | null
  ancestry_caveated: boolean
  cross_module: { module: string; note: string } | null
}

/** Summary of a single gene-health pathway. */
export interface PathwaySummary {
  pathway_id: string
  pathway_name: string
  level: PathwayLevel
  evidence_level: number
  called_snps: number
  total_snps: number
  missing_snps: string[]
  /** On-chip no-calls within missing_snps (#900); off-chip = missing_snps minus this. */
  no_call_snps?: string[]
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

/** All pathway results for a sample. */
export interface PathwaysResponse {
  items: PathwaySummary[]
  total: number
  cross_module: CrossModuleItem[]
  module_disclaimer: string | null
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
  /** On-chip no-calls within missing_snps (#900); off-chip = missing_snps minus this. */
  no_call_snps?: string[]
  pmids: string[]
  snp_details: SNPDetail[]
}
