/** Gene Fitness API types (P3-47). */

import type { SnpCategory } from "@/lib/snpCategory"

/** Categorical level for a fitness pathway. */
type PathwayLevel = "Elevated" | "Moderate" | "Standard"

/** Per-SNP result within a fitness pathway. */
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
  /** ACTN3 R577X three-state label (RR/RX/XX). */
  three_state_label: string | null
  /** ACE I/D proxy coverage caveat. */
  coverage_note: string | null
}

/** Summary of a single fitness pathway. */
export interface PathwaySummary {
  pathway_id: string
  pathway_name: string
  level: PathwayLevel
  evidence_level: number
  called_snps: number
  total_snps: number
  missing_snps: string[]
  /** rsIDs that were called but are strand-INDETERMINATE (palindromic A/T or
   * C/G homozygotes, e.g. FTO rs9939609) whose curated category cannot be
   * resolved from the array genotype alone. A Standard pathway carrying these
   * is NOT confidently clear (#270/#356). Optional: older cached responses may
   * omit it. */
  indeterminate_snps?: string[]
  pmids: string[]
}

/** Cross-pathway context finding (e.g. ACTN3 relevant to both Power and Endurance). */
export interface CrossContextItem {
  rsid: string
  gene: string
  source_pathway: string
  context_pathway: string
  finding_text: string
  evidence_level: number
  pmids: string[]
}

/** All pathway results for a sample. */
export interface PathwaysResponse {
  items: PathwaySummary[]
  total: number
  cross_context: CrossContextItem[]
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
