/** Pharmacogenomics API types (P3-06). */

/** Three-state calling confidence for star-allele calls. */
export type CallConfidence = "Complete" | "Partial" | "Insufficient"

/** Per-gene star-allele summary for metabolizer cards. */
export interface GeneSummary {
  gene: string
  diplotype: string | null
  phenotype: string | null
  call_confidence: CallConfidence | null
  confidence_note: string | null
  activity_score: number | null
  ehr_notation: string | null
  evidence_level: number | null
  involved_rsids: string[]
  drugs: string[]
  /**
   * Gene-specific interpretive caveat (context only — never changes the
   * metabolizer status or evidence level). Mirrors the backend constant
   * `DPYD_FLUOROPYRIMIDINE_CAVEAT` in backend/disclaimers.py (SW-E5).
   */
  gene_caveat: string | null
}

export interface GeneSummaryResponse {
  items: GeneSummary[]
  total: number
}

/** Drug list item from CPIC database. */
export interface DrugListItem {
  drug: string
  genes: string[]
  classification: string | null
}

export interface DrugListResponse {
  items: DrugListItem[]
  total: number
}

/** Per-gene genotype effect for a specific drug. */
export interface GeneEffect {
  gene: string
  diplotype: string | null
  metabolizer_status: string | null
  recommendation: string | null
  classification: string | null
  guideline_url: string | null
  call_confidence: CallConfidence | null
  confidence_note: string | null
  evidence_level: number | null
  activity_score: number | null
  ehr_notation: string | null
  involved_rsids: string[]
  /** See {@link GeneSummary.gene_caveat}. Mirrors a backend disclaimer (SW-E5). */
  gene_caveat: string | null
}

export interface DrugLookupResponse {
  drug: string
  gene_effects: GeneEffect[]
}

// ── Medication-safety report (SW-E4) ─────────────────────────────────

/** Coarse actionability label for a CPIC recommendation. Presentation aid only. */
type Actionability = "actionable" | "routine" | "indeterminate"

/**
 * SNP defining-position coverage for a pharmacogene: `assessed` of `total`
 * defining array positions were genotyped and called. SNP-level only — it
 * cannot reflect copy-number / gene-conversion alleles (see the reference-bias
 * disclosure).
 */
export interface CoverageInfo {
  assessed: number
  total: number
}

/** One gene's effect on a drug within the medication-safety report. */
export interface ReportGeneEffect {
  gene: string
  diplotype: string | null
  phenotype: string | null
  recommendation: string | null
  classification: string | null
  guideline_url: string | null
  call_confidence: CallConfidence | null
  confidence_note: string | null
  evidence_level: number | null
  activity_score: number | null
  ehr_notation: string | null
  coverage: CoverageInfo | null
  actionability: Actionability
  /** See {@link GeneSummary.gene_caveat}. Mirrors a backend disclaimer. */
  gene_caveat: string | null
}

/** All gene effects for one drug, with a drug-level actionability flag. */
export interface DrugSafetyEntry {
  drug: string
  actionable: boolean
  gene_effects: ReportGeneEffect[]
}

/** Per-gene coverage / call-confidence summary for the report header. */
export interface GeneCoverageSummary {
  gene: string
  diplotype: string | null
  phenotype: string | null
  call_confidence: CallConfidence | null
  confidence_note: string | null
  coverage: CoverageInfo | null
  activity_score: number | null
  ehr_notation: string | null
  evidence_level: number | null
  gene_caveat: string | null
}

/**
 * Consolidated drug-centric medication-safety report for a sample (SW-E4).
 * `reference_bias_disclosure` mirrors the backend constant
 * `MEDICATION_SAFETY_REFERENCE_BIAS` in backend/disclaimers.py.
 */
export interface MedicationSafetyReportResponse {
  reference_bias_disclosure: string
  genes_assessed: number
  drugs_assessed: number
  actionable_drug_count: number
  gene_coverage: GeneCoverageSummary[]
  drugs: DrugSafetyEntry[]
}
