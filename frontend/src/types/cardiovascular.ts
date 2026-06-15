/** Cardiovascular module API types (P3-21). */

/** A single P/LP variant in the cardiovascular panel. */
export interface CardiovascularVariant {
  rsid: string
  gene_symbol: string
  genotype: string | null
  zygosity: string | null
  clinvar_significance: string
  clinvar_accession: string | null
  clinvar_review_stars: number
  clinvar_conditions: string | null
  conditions: string[]
  cardiovascular_category: string
  inheritance: string
  evidence_level: number
  cross_links: string[]
  pmids: string[]
}

/** All cardiovascular P/LP findings for a sample. */
export interface CardiovascularVariantsListResponse {
  items: CardiovascularVariant[]
  total: number
}

/** Summary of a single FH variant within the FH status response. */
interface FHVariantSummary {
  rsid: string
  gene_symbol: string
  genotype: string | null
  zygosity: string | null
  clinvar_significance: string
  clinvar_review_stars: number
  clinvar_accession: string | null
  evidence_level: number
}

/** FH status determination for a sample (P3-20). */
export interface FHStatusResponse {
  status: "Positive" | "Negative"
  summary_text: string
  affected_genes: string[]
  variant_count: number
  has_homozygous: boolean
  highest_evidence_level: number
  variants: FHVariantSummary[]
}

/** Cardiovascular module disclaimer text. */
export interface CardiovascularDisclaimerResponse {
  title: string
  text: string
}
