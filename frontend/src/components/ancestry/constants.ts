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

/** Neutral color for an unknown population or missing delivered color. */
const FALLBACK_POPULATION_COLOR = "#94A3B8"
const HEX_COLOR_RE = /^#[0-9A-Fa-f]{6}$/

export type PopulationColorMap = Record<string, string>

/**
 * Population code → fallback color mapping for charts.
 *
 * These values mirror the backend's Paul Tol palette. LAI results can override
 * them at runtime through `resolvePopulationColors`, but pages without LAI data
 * still use the same population encoding.
 */
export const POPULATION_COLORS: Record<string, string> = {
  AFR: "#E8A838",
  AMR: "#EE6677",
  CSA: "#AA3377",
  EAS: "#66CCEE",
  EUR: "#4477AA",
  MID: "#228833",
  OCE: "#CCBB44",
}

export function getPopulationColor(
  populationColors: PopulationColorMap,
  population: string,
): string {
  return populationColors[population] ?? FALLBACK_POPULATION_COLOR
}

/** Resolve one page-wide palette from backend data and canonical fallbacks. */
export function resolvePopulationColors(
  globalAncestry?: Record<string, { color?: string | null }> | null,
  painting?: Record<
    string,
    Array<{
      hap0: string
      hap1: string
      hap0_color?: string | null
      hap1_color?: string | null
    }>
  > | null,
): PopulationColorMap {
  const colors: PopulationColorMap = { ...POPULATION_COLORS }

  const setDeliveredColor = (population: string, color?: string | null) => {
    if (typeof population !== "string" || typeof color !== "string") return
    const normalized = color.trim()
    if (population && HEX_COLOR_RE.test(normalized)) colors[population] = normalized
  }

  for (const segments of Object.values(painting ?? {})) {
    for (const segment of segments) {
      setDeliveredColor(segment.hap0, segment.hap0_color)
      setDeliveredColor(segment.hap1, segment.hap1_color)
    }
  }

  // Global ancestry is the page-level contract and therefore wins if a legacy
  // payload happens to disagree with a segment-level color.
  for (const [population, entry] of Object.entries(globalAncestry ?? {})) {
    setDeliveredColor(population, entry.color)
  }

  return colors
}

/** Canonical population order for consistent display. */
export const POPULATION_ORDER = ["AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE"] as const
