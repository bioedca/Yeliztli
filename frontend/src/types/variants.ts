/** Variant types matching backend Pydantic models (P1-14). */

export interface VariantRow {
  rsid: string
  chrom: string
  pos: number
  genotype: string
  ref: string | null
  alt: string | null
  zygosity: string | null
  gene_symbol: string | null
  consequence: string | null
  clinvar_significance: string | null
  clinvar_review_stars: number | null
  gnomad_af_global: number | null
  rare_flag: boolean | null
  cadd_phred: number | null
  sift_score: number | null
  sift_pred: string | null
  polyphen2_hsvar_score: number | null
  polyphen2_hsvar_pred: string | null
  revel: number | null
  annotation_coverage: number | null
  evidence_conflict: boolean | null
  ensemble_pathogenic: boolean | null
  chrom_grch38: string | null
  pos_grch38: number | null
  tags?: string[] | null
  /** Merge-provenance columns (AncestryDNA Plan §10.4 / Step 71).
   *  Server-default '' on unmerged samples; on merged samples the backend
   *  LEFT-JOINs raw_variants to surface them through the variants list. */
  source?: SourceTag | ""
  concordance?: ConcordanceTag | ""
  alt_rsid?: string
}

/** Per-row source attribution on a merged sample (Plan §10.4). */
export type SourceTag = "S1" | "S2" | "both"

/** Per-row concordance bucket on a merged sample (Plan §10.4). */
export type ConcordanceTag = "match" | "filled_nocall" | "discordant" | "unique"

export const SOURCE_OPTIONS: readonly SourceTag[] = ["S1", "S2", "both"] as const
export const CONCORDANCE_OPTIONS: readonly ConcordanceTag[] = [
  "match",
  "filled_nocall",
  "discordant",
  "unique",
] as const

/** Human-readable labels for the merged-sample filter chips. */
export const SOURCE_LABELS: Record<SourceTag, string> = {
  S1: "S₁",
  S2: "S₂",
  both: "Both",
}

export const CONCORDANCE_LABELS: Record<ConcordanceTag, string> = {
  match: "Match",
  filled_nocall: "Filled no-call",
  discordant: "Discordant",
  unique: "Unique",
}

export interface VariantPage {
  items: VariantRow[]
  next_cursor_chrom: string | null
  next_cursor_pos: number | null
  has_more: boolean
  limit: number
}

export interface VariantCount {
  total: number
  filtered: boolean
}

/** Cursor used for keyset pagination. */
export interface VariantCursor {
  chrom: string
  pos: number
}

/** Per-chromosome variant count for the chromosome nav bar (P1-15b). */
export interface ChromosomeSummary {
  chrom: string
  count: number
}

/** Canonical chromosome list in display order. */
export const CHROMOSOMES = [
  "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
  "11", "12", "13", "14", "15", "16", "17", "18", "19", "20",
  "21", "22", "X", "Y", "MT",
] as const

/** Per-chromosome QC breakdown (P1-21). */
export interface ChromosomeQCStats {
  chrom: string
  total: number
  het_count: number
  hom_count: number
  nocall_count: number
}

/** Aggregate QC statistics for a sample (P1-21). */
export interface QCStats {
  total_variants: number
  called_variants: number
  nocall_variants: number
  het_count: number
  hom_count: number
  call_rate: number
  heterozygosity_rate: number
  per_chromosome: ChromosomeQCStats[]
}

/** Single genomic bin in the density histogram (P2-23). */
export interface DensityBin {
  chrom: string
  bin_start: number
  bin_end: number
  high: number
  moderate: number
  low: number
  modifier: number
  total: number
}

/** Single consequence type with count and tier (P2-25). */
export interface ConsequenceCount {
  consequence: string
  count: number
  tier: string
}

/** Single ClinVar significance category with count (P2-26). */
export interface ClinvarSignificanceCount {
  significance: string
  count: number
}

/** Lightweight variant search result for the command palette (P4-26e). */
export interface VariantSearchResult {
  rsid: string
  chrom: string
  pos: number
  gene_symbol: string | null
  clinvar_significance: string | null
}

/** Column preset profile (P1-15c). */
export interface ColumnPreset {
  name: string
  columns: string[]
  predefined: boolean
}

/** Tag for variant classification (P4-12b). */
export interface Tag {
  id: number
  name: string
  color: string
  is_predefined: boolean
  created_at: string | null
  variant_count: number | null
}
