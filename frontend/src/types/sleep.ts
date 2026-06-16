/** Gene Sleep API types (P3-50). */

/** Categorical level for a sleep pathway. */
type PathwayLevel = "Elevated" | "Moderate" | "Standard"

/** Per-SNP result within a sleep pathway. */
export interface SNPDetail {
  rsid: string
  gene: string
  variant_name: string
  genotype: string | null
  category: PathwayLevel
  effect_summary: string
  evidence_level: number
  recommendation: string | null
  pmids: string[]
  /** CYP1A2 metabolizer state (rapid/intermediate/slow). */
  metabolizer_state: string | null
  /** HLA-DQB1*06:02 proxy or PER3 VNTR coverage caveat. */
  coverage_note: string | null
}

/** Summary of a single sleep pathway. */
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

/** Cross-module reference finding (CYP1A2 → Pharmacogenomics). */
export interface CrossModuleItem {
  rsid: string
  gene: string
  source_module: string
  target_module: string
  finding_text: string
  evidence_level: number
  pmids: string[]
}

/** CYP1A2 caffeine metabolizer state. */
export interface MetabolizerState {
  state: string | null
  gene: string
  rsid: string
}

/** All pathway results for a sample. */
export interface PathwaysResponse {
  items: PathwaySummary[]
  total: number
  cross_module: CrossModuleItem[]
  metabolizer: MetabolizerState | null
}

/** Full pathway detail with per-SNP breakdown. */
export interface PathwayDetailResponse extends PathwaySummary {
  snp_details: SNPDetail[]
}
