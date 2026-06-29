/** Ancestry module shared constants (P3-27, AMv2 Step 5).
 *
 * Canonical 7-population order: AFR, AMR, CSA, EAS, EUR, MID, OCE.
 * Updated from 6 populations (SAS→CSA rename, MID addition).
 */

/** Population code → display label mapping. */
export const POPULATION_LABELS: Record<string, string> = {
  AFR: "African",
  AMR: "Admixed American",
  CSA: "Central/South Asian",
  EAS: "East Asian",
  EUR: "European",
  MID: "Middle Eastern",
  OCE: "Oceanian",
}

/** Whole-word matcher for the raw population codes (EUR/MID/AMR/…). */
const POPULATION_CODE_RE = new RegExp(`\\b(${Object.keys(POPULATION_LABELS).join("|")})\\b`, "g")

/** Replace raw 3-letter population codes in a human-facing string with their
 *  POPULATION_LABELS display names, so a backend-authored summary (e.g.
 *  "Inferred ancestry: EUR 72%, MID 27%, AMR 1% (…)") reads consistently with
 *  the top-population badge and the Population Ranking, which already humanize
 *  via the same map (#1225). Whole-word matches only; any non-code text — the
 *  "Inferred ancestry:" / "Admixed / low-confidence…" framing, the markers /
 *  coverage suffix, an uncertain-finding sentence with no codes — passes
 *  through unchanged. */
export function humanizeAncestryCodes(text: string): string {
  return text.replace(POPULATION_CODE_RE, (code) => POPULATION_LABELS[code] ?? code)
}

/** Population code → color mapping for charts. */
export const POPULATION_COLORS: Record<string, string> = {
  AFR: "#F59E0B",  // amber-500
  AMR: "#EF4444",  // red-500
  CSA: "#8B5CF6",  // violet-500
  EAS: "#10B981",  // emerald-500
  EUR: "#3B82F6",  // blue-500
  MID: "#14B8A6",  // teal-500
  OCE: "#EC4899",  // pink-500
}

/** Canonical population order for consistent display. */
export const POPULATION_ORDER = ["AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE"] as const
