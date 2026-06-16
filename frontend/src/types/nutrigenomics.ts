/** Nutrigenomics API types (P3-11). */

import type { SnpCategory } from "@/lib/snpCategory"

/** Categorical consideration level for a nutrient pathway. */
type PathwayLevel = "Elevated" | "Moderate" | "Standard"

/** Per-SNP result within a pathway. */
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
}

/** Summary of a single nutrient pathway. */
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

/** All pathway results for a sample. */
export interface PathwaysResponse {
  items: PathwaySummary[]
  total: number
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
