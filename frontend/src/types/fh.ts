/** Familial hypercholesterolemia (FH) view API types (SW-B6). */

import type { ScoringState } from "@/types/route-scoring"

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
  snps_used_imputed: number
  snps_total: number
  coverage_fraction: number
  coverage_tier: "typed_only" | "imputed"
  is_sufficient: boolean
  source_ancestry: string
  source_study: string
  source_pmid: string
  sample_size: number
  pgs_id: string | null
  pgs_license: string | null
  development_method: string | null
  genome_build: string | null
  variants_number: number | null
  source_url: string | null
  ancestry_mismatch: boolean
  ancestry_warning_text: string | null
  evidence_level: number
}

export interface FhAssessment {
  assessment_status: "available" | "unavailable"
  has_monogenic: boolean
  monogenic: FhMonogenic[]
  apob_fdb: ApobFdb | null
  ldl_prs: FhLdlPrs | null
  criteria_context: Record<string, string>
  research_use_only: boolean
  scoring_state: ScoringState
}
