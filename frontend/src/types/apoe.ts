/** APOE module API types (P3-22d). */

/** APOE gate disclosure text from disclaimers.py. */
export interface APOEGateDisclaimerResponse {
  title: string
  text: string
  accept_label: string
  decline_label: string
}

/** Current APOE gate acknowledgment state for a sample. */
export interface APOEGateStatusResponse {
  acknowledged: boolean
  acknowledged_at: string | null
}

/** Basic APOE genotype information (not gate-protected). */
export interface APOEGenotypeResponse {
  status: "determined" | "missing_snps" | "no_call" | "ambiguous" | "not_run"
  diplotype: string | null
  has_e4: boolean | null
  e4_count: number | null
  has_e2: boolean | null
  e2_count: number | null
  rs429358_genotype: string | null
  rs7412_genotype: string | null
}

/** A single APOE finding (CV risk, Alzheimer's, lipid/dietary). */
export interface APOEFinding {
  category: string
  evidence_level: number
  finding_text: string
  phenotype: string | null
  conditions: string | null
  diplotype: string | null
  pmid_citations: string[]
  detail_json: Record<string, unknown>
}

/** All APOE findings for a sample (gate-protected). */
export interface APOEFindingsListResponse {
  items: APOEFinding[]
  total: number
}
