/** Cross-source pharmacogenomic evidence types (SW-E2).
 *
 * Mirrors backend `routes/pgx_guidelines.py` (PgxGuidelinesResponse /
 * PgxAlertSourcesResponse). Context-only: PharmGKB Level of Evidence + DPWG
 * guideline presence + FDA PGx labeling layered over the sample's CPIC alerts;
 * it never changes a finding, a CPIC recommendation, or a metabolizer status.
 */

export interface PgxAlertSources {
  finding_id: number
  gene_symbol: string | null
  drug: string | null
  metabolizer_status: string | null
  /** False = the (gene, drug) pair is not in the curated table (absence of
   * corroboration, NOT a downgrade). */
  has_sources: boolean
  pharmgkb_loe: string | null
  dpwg_guideline: boolean | null
  fda_pgx_level: string | null
}

export interface PgxGuidelinesResponse {
  alerts: PgxAlertSources[]
  context_only: boolean
  note: string
  pmid_citations: string[]
}
