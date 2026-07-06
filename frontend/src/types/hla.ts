/** HLA (imputed) module API types (Wave D). */

export type HlaDrugRiskStatus = "at_risk" | "low_confidence" | "no_risk_allele" | "not_typed"

export interface HlaDrugRiskAssessment {
  allele: string
  drugs: string[]
  reaction: string
  status: HlaDrugRiskStatus
  carried: boolean
  zygosity: string | null
  copies: number
  prob: number | null
  low_confidence: boolean
  recommendation: string
  guideline: string
  citations: string[]
  notes: string[]
}

export interface HlaDrugHypersensitivityResponse {
  available: boolean
  any_at_risk: boolean
  assessments: HlaDrugRiskAssessment[]
  caveat: string
  unavailable_note: string | null
  research_use_only: boolean
}

type CeliacRuleOutStatus = "rule_out" | "permissive_present" | "not_typed"
type NarcolepsyRuleOutStatus = "absent_lowers" | "present" | "not_typed"

export interface CeliacRuleOut {
  status: CeliacRuleOutStatus
  detected: string[]
  low_confidence: boolean
  interpretation: string
}

export interface NarcolepsyRuleOut {
  status: NarcolepsyRuleOutStatus
  carried: boolean
  zygosity: string | null
  low_confidence: boolean
  interpretation: string
}

export interface HlaRuleOutsResponse {
  available: boolean
  celiac: CeliacRuleOut | null
  narcolepsy: NarcolepsyRuleOut | null
  caveat: string
  unavailable_note: string | null
  citations: string[]
  research_use_only: boolean
}

export type HlaSusceptibilityStatus =
  | "increased_risk"
  | "not_increased"
  | "neutral_subtype"
  | "not_typed"

export interface HlaSusceptibilityFinding {
  condition: string
  hla: string
  status: HlaSusceptibilityStatus
  carried: boolean
  detail: string
  interpretation: string
  low_confidence: boolean
  citations: string[]
  notes: string[]
}

export interface HlaSusceptibilityResponse {
  available: boolean
  findings: HlaSusceptibilityFinding[]
  caveat: string
  unavailable_note: string | null
  research_use_only: boolean
}

/** One imputed per-locus HLA genotype row. @public — element type of HlaViewerResponse.alleles, for downstream consumers. */
export interface HlaAlleleView {
  locus: string
  allele1: string
  allele2: string
  prob: number | null
  low_confidence: boolean
  source: string
  ancestry_model: string | null
}

export interface HlaViewerResponse {
  available: boolean
  alleles: HlaAlleleView[]
  caveat: string
  transplant_guard: string
  unavailable_note: string | null
  research_use_only: boolean
}
