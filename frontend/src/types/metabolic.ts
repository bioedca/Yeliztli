/** Metabolic module API types (SW-B5): T2D & obesity PRS + anchor SNPs. */

import type { CancerPRS } from "@/types/cancer"

/** A metabolic PRS result (uncalibrated; coverage reported, percentile withheld). */
export interface MetabolicPRS {
  trait: string
  name: string
  calibrated: boolean
  percentile: number | null
  snps_used: number
  snps_total: number
  coverage_fraction: number
  is_sufficient: boolean
  source_ancestry: string
  source_study: string
  source_pmid: string
  sample_size: number
  ancestry_mismatch: boolean
  ancestry_warning_text: string | null
  evidence_level: number
  research_use_only: boolean
  pgs_id: string | null
  pgs_license: string | null
  development_method: string | null
  genome_build: string | null
  variants_number: number | null
  source_url: string | null
}

export interface MetabolicPRSListResponse {
  items: MetabolicPRS[]
  total: number
  coverage_context: string
}

/** A single established anchor-SNP result (TCF7L2 / FTO / MC4R). */
export interface MetabolicAnchor {
  trait: string
  trait_label: string
  gene: string
  rsid: string
  effect_allele: string
  genotype: string | null
  dosage: number
  summary: string
  evidence_level: number
  pmids: string[]
}

export interface MetabolicAnchorListResponse {
  items: MetabolicAnchor[]
  total: number
}

/**
 * Adapt a coverage-reported PRS (metabolic/FH/eBMD) to the CancerPRS shape that
 * PRSGaugeCard renders. These scores are uncalibrated, so the CI/z-score/sample
 * fields the gauge reads are simply absent (the card shows "coverage too low").
 */
export function toGaugePrs(p: {
  trait: string
  name: string
  calibrated: boolean
  percentile: number | null
  snps_used: number
  snps_total: number
  coverage_fraction: number
  is_sufficient: boolean
  source_ancestry?: string
  source_study: string
  source_pmid?: string
  sample_size?: number
  ancestry_mismatch: boolean
  ancestry_warning_text: string | null
  evidence_level: number
  pgs_id: string | null
  pgs_license: string | null
  development_method: string | null
  genome_build: string | null
  variants_number: number | null
  source_url: string | null
}): CancerPRS {
  return {
    trait: p.trait,
    name: p.name,
    calibrated: p.calibrated,
    percentile: p.percentile,
    z_score: null,
    bootstrap_ci_lower: null,
    bootstrap_ci_upper: null,
    bootstrap_iterations: 0,
    snps_used: p.snps_used,
    snps_total: p.snps_total,
    coverage_fraction: p.coverage_fraction,
    is_sufficient: p.is_sufficient,
    source_ancestry: p.source_ancestry ?? "",
    source_study: p.source_study,
    source_pmid: p.source_pmid ?? "",
    sample_size: p.sample_size ?? 0,
    ancestry_mismatch: p.ancestry_mismatch,
    ancestry_warning_text: p.ancestry_warning_text,
    evidence_level: p.evidence_level,
    research_use_only: true,
    pgs_id: p.pgs_id,
    pgs_license: p.pgs_license,
    development_method: p.development_method,
    genome_build: p.genome_build,
    variants_number: p.variants_number,
    source_url: p.source_url,
    monogenic_genes: [],
    monogenic_carrier_genes: [],
    monogenic_note: null,
  }
}
