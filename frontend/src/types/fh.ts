/** Familial hypercholesterolemia (FH) view API types (SW-B6). */

interface FhMonogenic {
  gene: string
  rsid: string | null
  clinvar_significance: string | null
  zygosity: string | null
  evidence_level: number
}

interface ApobFdb {
  rsid: string
  gene: string
  protein: string
  genotype: string | null
  clinvar_significance: string | null
  is_pathogenic: boolean
}

interface FhLdlPrs {
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

export interface FhAssessment {
  has_monogenic: boolean
  monogenic: FhMonogenic[]
  apob_fdb: ApobFdb | null
  ldl_prs: FhLdlPrs | null
  criteria_context: Record<string, string>
  research_use_only: boolean
}
