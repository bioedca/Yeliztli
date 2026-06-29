/** Carrier status module API types (P3-38). */

export const DEFAULT_COPY_NUMBER_CAVEAT =
  "Copy-number not assessed: SNP-array data do not measure SMN1 exon 7 dosage/copy-number. Confirm SMN1 status with clinical testing that includes dosage/CNV assessment, such as qPCR or MLPA."

interface CarrierComponentVariant {
  rsid: string
  chrom: string | null
  pos: number | null
  ref: string | null
  alt: string | null
  genotype: string | null
  zygosity: string
  clinvar_significance: string | null
  clinvar_review_stars: number
  clinvar_accession: string | null
  clinvar_conditions: string | null
}

/** A single carrier-module carrier or affected-status finding in the panel. */
export interface CarrierVariant {
  rsid: string
  gene_symbol: string
  genotype: string | null
  zygosity: string
  clinvar_significance: string
  clinvar_accession: string | null
  clinvar_review_stars: number
  clinvar_conditions: string | null
  conditions: string[]
  inheritance: string
  clinvar_low_penetrance_or_risk_allele?: boolean
  evidence_level: number
  cross_links: string[]
  pmids: string[]
  notes: string
  category?: string | null
  finding_type?: "carrier" | "affected_homozygous" | "possible_compound_heterozygote"
  variant_ids?: string[]
  component_variants?: CarrierComponentVariant[]
  phase_caveat?: string | null
  copy_number_limited?: boolean
  copy_number_caveat?: string | null
}

/** All carrier-module findings for a sample. */
export interface CarrierVariantsListResponse {
  items: CarrierVariant[]
  total: number
  genes_with_findings: string[]
}

/** Carrier status disclaimer with per-gene notes. */
export interface CarrierDisclaimerResponse {
  title: string
  text: string
  gene_notes: Record<string, string>
}

/** Shared inheritance mode labels used across carrier components. */
export const INHERITANCE_LABELS: Record<string, string> = {
  AD: "Autosomal Dominant",
  AR: "Autosomal Recessive",
  XL: "X-linked",
}
