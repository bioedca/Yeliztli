/** Gene Allergy API types (P3-61). */

import type { SnpCategory } from "@/lib/snpCategory"

/** Categorical level for an allergy pathway. */
type PathwayLevel = "Elevated" | "Moderate" | "Standard"

/**
 * HLA proxy metadata for a SNP — the curated `hla_proxy` block from the panel
 * definition (backend `sd["hla_proxy"]`). It has NO singular `r_squared` /
 * `ancestry_pop`; per-population r² lives either in `r_squared_by_population`
 * or in legacy `r_squared_<pop>` keys (e.g. `r_squared_eur`). The clean
 * per-population r² for display comes from the sibling `HLAProxyLookup`.
 */
interface HLAProxyInfo {
  hla_allele: string
  clinical_grade?: boolean
  clinical_grade_context?: string | null
  confirmatory_test_required?: boolean
  /** Post-#333 per-population r² map, when present. */
  r_squared_by_population?: Record<string, number>
  /** Tolerate legacy per-population r² keys such as `r_squared_eur`. */
  [key: string]: unknown
}

/**
 * Runtime HLA proxy lookup result (backend `hla_proxy_lookup`): the clean
 * per-population r² source. Null when the proxy SNP has no lookup row.
 */
interface HLAProxyLookup {
  hla_allele?: string
  r_squared_by_pop?: Record<string, number>
  clinical_context?: string | null
}

/** Per-SNP result within an allergy pathway. */
export interface SNPDetail {
  rsid: string
  gene: string
  variant_name: string
  genotype: string | null
  /**
   * Per-SNP effect category. Widens the pathway-level `PathwayLevel` with the
   * runtime-only `Indeterminate` value (#369/#465): a strand-ambiguous
   * palindromic homozygote whose call the backend withholds (#269/#436, e.g.
   * AOC1 rs1049793 CC/GG). Rendered neutral slate, never green Standard.
   */
  category: SnpCategory
  effect_summary: string
  evidence_level: number
  recommendation: string | null
  pmids: string[]
  /** HLA proxy metadata from panel definition. */
  hla_proxy: HLAProxyInfo | null
  /** HLA proxy lookup result from reference DB (clean per-population r²). */
  hla_proxy_lookup: HLAProxyLookup | null
  /** Coverage caveat text. */
  coverage_note: string | null
}

/** Summary of a single allergy pathway. */
export interface PathwaySummary {
  pathway_id: string
  pathway_name: string
  level: PathwayLevel
  evidence_level: number
  called_snps: number
  total_snps: number
  missing_snps: string[]
  pmids: string[]
  hla_proxy_lookup: Record<string, unknown> | null
}

/** Celiac DQ2/DQ8 combined assessment result. */
export interface CeliacCombinedItem {
  state: "neither" | "dq2_only" | "dq8_only" | "both"
  label: string
  dq2_genotype: string | null
  dq8_genotype: string | null
  description: string | null
  evidence_level: number
  pmids: string[]
}

/** Histamine metabolism combined assessment result. */
export interface HistamineCombinedItem {
  aoc1_genotype: string | null
  hnmt_genotype: string | null
  aoc1_category: string
  hnmt_category: string
  combined_text: string
  de_emphasize: boolean
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

/** All pathway results for a sample. */
export interface PathwaysResponse {
  items: PathwaySummary[]
  total: number
  celiac_combined: CeliacCombinedItem | null
  histamine_combined: HistamineCombinedItem | null
  cross_module: CrossModuleItem[]
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
  hla_proxy_lookup: Record<string, unknown> | null
}
