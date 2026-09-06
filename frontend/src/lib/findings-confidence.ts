/** The evidence-level threshold that makes a finding "high confidence".
 *
 * Mirrors `backend/api/routes/findings.py`, which selects the dashboard preview
 * with `(r.evidence_level or 0) >= 3`. Defined once here because the value was
 * previously inlined at each call site, and a threshold that drifts from the
 * backend silently relabels which findings count as high confidence.
 * `findings-confidence.test.ts` pins it against the backend source.
 */
export const HIGH_CONFIDENCE_MIN_STARS = 3

/** URL parameter FindingsExplorer seeds its min-stars filter from. */
export const MIN_STARS_PARAM = "minStars"

/** Parse a `minStars` query value, ignoring anything outside the 1-4 star range. */
export function parseMinStars(raw: string | null): number | null {
  if (raw === null) return null
  const value = Number(raw)
  if (!Number.isInteger(value) || value < 1 || value > 4) return null
  return value
}
