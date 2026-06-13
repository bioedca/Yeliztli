/** MTHFR & Methylation API types (P3-53). */

import type { SnpCategory } from "@/lib/snpCategory"

/** Categorical consideration level for a methylation pathway. */
export type PathwayLevel = "Elevated" | "Moderate" | "Standard"

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
  coverage_note: string | null
}

/** Summary of a single methylation pathway. */
export interface PathwaySummary {
  pathway_id: string
  pathway_name: string
  level: PathwayLevel
  evidence_level: number
  called_snps: number
  total_snps: number
  missing_snps: string[]
  pmids: string[]
  additive_promoted: boolean
}

/** MTHFR compound heterozygosity info. */
export interface CompoundHetInfo {
  is_compound_het: boolean
  is_double_homozygous: boolean
  label: string | null
  c677t_genotype: string | null
  a1298c_genotype: string | null
  finding_text: string | null
}

/** All pathway results for a sample. */
export interface PathwaysResponse {
  items: PathwaySummary[]
  total: number
  compound_het: CompoundHetInfo | null
}

/** Full pathway detail with per-SNP breakdown. */
export interface PathwayDetailResponse extends PathwaySummary {
  snp_details: SNPDetail[]
}
