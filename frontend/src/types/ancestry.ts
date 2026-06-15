/** Ancestry module API types (P3-27). */

/** Distance to a reference population centroid. */
interface PopulationDistance {
  population: string
  distance: number
}

/** Ancestry inference finding response. */
export interface AncestryFindingResponse {
  top_population: string
  pc_scores: number[]
  population_distances: Record<string, number>
  admixture_fractions: Record<string, number>
  population_ranking: PopulationDistance[]
  snps_used: number
  snps_total: number
  coverage_fraction: number
  projection_time_ms: number
  is_sufficient: boolean
  evidence_level: number
  finding_text: string
  /** NNLS-kNN confidence (cosine similarity, 0–1). */
  confidence: number
  /** Fraction of AIMs missing from user data (0–1). */
  missing_aim_rate: number
  /** Admixture estimation method ("nnls" or "idw"). */
  admixture_method: string
  /** Number of principal components used. */
  n_pcs_used: number
  /** Per-population NNLS fractions (may be null if not computed). */
  nnls_fractions: Record<string, number> | null
  /** Per-population kNN fractions (may be null if not computed). */
  knn_fractions: Record<string, number> | null
  /** Bootstrap 95% CI lower bound per population (NNLS). */
  nnls_ci_low: Record<string, number> | null
  /** Bootstrap 95% CI upper bound per population (NNLS). */
  nnls_ci_high: Record<string, number> | null
}

/** PCA coordinates for scatter plot visualization (P3-25). */
export interface PCACoordinatesResponse {
  user: number[]
  reference_samples: Record<string, number[][]>
  centroids: Record<string, number[]>
  population_labels: Record<string, string>
  n_components: number
  pc_labels: string[]
  top_population: string
}

/** A single step in the haplogroup traversal path (P3-34). */
interface HaplogroupTraversalStep {
  haplogroup: string
  snps_present: number
  snps_total: number
}

/** A haplogroup assignment for a single tree (mt or Y) (P3-34). */
export interface HaplogroupAssignment {
  type: string
  haplogroup: string
  confidence: number
  defining_snps_present: number
  defining_snps_total: number
  traversal_path: HaplogroupTraversalStep[]
  finding_text: string
}

/** Haplogroup assignments response (P3-34). */
export interface HaplogroupResponse {
  assignments: HaplogroupAssignment[]
}

/** LAI bundle and Java availability status.
 *
 * `degraded_coverage` is the Step-23 soft-gate flag (Plan §6.7): True
 * when the installed `lai_bundle` is pre-v2.0.0 *and* the install holds
 * at least one AncestryDNA-sourced sample. Drives the dashboard
 * `<AppUpdateBanner>` advisory.
 */
export interface LAIStatusResponse {
  bundle_downloaded: boolean
  java_available: boolean
  lai_available: boolean
  message: string
  degraded_coverage?: boolean
}

/** Response from triggering LAI analysis.
 *
 * Carries the Step-23 `degraded_coverage` advisory flag (Plan §6.7) so
 * the LAI Findings page can render a per-sample banner during the run.
 */
export interface LAITriggerResponse {
  job_id: string
  message: string
  degraded_coverage?: boolean
}

/** Per-population global ancestry from LAI. */
export interface LAIGlobalAncestryEntry {
  fraction: number
  percentage: number
  display_name: string
  color: string
  /** Per-population window confidence from LAI (mean softmax probability). */
  confidence?: number
  /** Warning text, e.g. for MID lower-precision. */
  warning?: string
}

/** A single segment in chromosome painting (one window). */
export interface ChromosomePaintingSegment {
  start: number
  end: number
  n_snps: number
  hap0: string
  hap1: string
  hap0_color: string
  hap1_color: string
}

/** Per-source LAI rsID hit / drop counts (Step 24, Plan §6.6/§6.7).
 *
 * Unmerged samples emit a single bucket keyed by vendor
 * (e.g. `"ancestrydna"` / `"23andme"`). Merged samples emit the three
 * uppercase buckets `S1` / `S2` / `both` matching `raw_variants.source`.
 */
export interface LAICoverageSourceTelemetry {
  hits: number
  drops: number
}

/** LAI coverage telemetry surfaced to `AncestryView` (Step 24, Plan §6.7).
 *
 * Powers the "X of Y rsIDs mapped to bundle (Z% dropout)" summary line
 * and the three-row source-breakdown table for merged samples. The
 * `drop_rate_warning` flag triggers the per-sample reduced-coverage
 * toast (Plan §6.6 — drop rate above 15%).
 */
export interface LAICoverageTelemetry {
  per_source: Record<string, LAICoverageSourceTelemetry>
  total_hits: number
  total_drops: number
  drop_rate: number
  drop_rate_warning: boolean
}

/** LAI analysis results.
 *
 * `degraded_coverage` (Step 23, Plan §6.7) is True when the run was
 * produced against a pre-v2.0.0 bundle for an AncestryDNA-sourced
 * sample. `coverage_telemetry` (Step 24, Plan §6.7) carries the
 * per-source rsID hit/drop counts the runner emits on every LAI run.
 */
export interface LAIResultResponse {
  global_ancestry: Record<string, LAIGlobalAncestryEntry>
  chromosome_painting: Record<string, ChromosomePaintingSegment[]>
  metadata: Record<string, unknown>
  created_at: string
  degraded_coverage?: boolean
  coverage_telemetry?: LAICoverageTelemetry | null
}

/** LAI analysis progress. */
export interface LAIProgressResponse {
  job_id: string
  status: "pending" | "running" | "complete" | "failed"
  progress_pct: number
  message: string
  error: string | null
  degraded_coverage?: boolean
}
