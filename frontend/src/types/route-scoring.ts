/**
 * Whether a route-only score (Metabolic, FH, eBMD) matches its sample's inputs (#2118).
 *
 * "never": no successful run recorded. "stale": an input it reads (variants,
 * ancestry, installed scores, reference data or the app) changed since.
 */
export type ScoringState = "current" | "stale" | "never"
