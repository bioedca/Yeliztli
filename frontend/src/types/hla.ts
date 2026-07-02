/** HLA (imputed) module API types (Wave D). */

export type HlaDrugRiskStatus = "at_risk" | "no_risk_allele" | "not_typed"

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
