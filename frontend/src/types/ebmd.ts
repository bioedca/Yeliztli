/** Osteoporosis / eBMD module API types (SW-B7, bring-your-own). */

export interface EbmdPrs {
  name: string
  calibrated: boolean
  percentile: number | null
  snps_used: number
  snps_total: number
  coverage_fraction: number
  is_sufficient: boolean
  source_study: string
  source_pmid: string
  pgs_id: string | null
  pgs_license: string | null
  development_method: string | null
  ancestry_mismatch: boolean
  ancestry_warning_text: string | null
  evidence_level: number
}

export interface EbmdResponse {
  available: boolean
  recommended_pgs_id: string
  prs: EbmdPrs | null
  context: Record<string, string>
  research_use_only: boolean
}
