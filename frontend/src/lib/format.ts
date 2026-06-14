/** Shared formatting utilities (P1-16). */

/** Format a raw file_format string (e.g. "23andme_v5") for display. */
export function formatFileFormat(fileFormat: string | null | undefined): string {
  if (!fileFormat) return "Unknown format"
  return fileFormat.replace("23andme_", "23andMe ").toUpperCase()
}

/** Parse a numeric query param safely, returning null for invalid values. */
export function parseSampleId(raw: string | null): number | null {
  if (!raw) return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

/** Format a number with locale-appropriate separators (e.g. 623841 → "623,841"). */
export function formatNumber(n: number): string {
  return n.toLocaleString()
}

/**
 * Format a gnomAD allele frequency as a raw fraction — the single source of
 * truth for AF display across views (#564).
 *
 * AF is a fraction by definition, so this never converts to a percentage:
 * mixing a percentage for common variants with a bare fraction for rare ones
 * (the previous Rare Variant Finder behavior) makes near-identical frequencies
 * look ~100× apart and uncomparable by eye. Very small frequencies use
 * scientific notation so they keep their significant digits; ``null`` (variant
 * absent from gnomAD) is the caller's affordance — callers that mean "Novel"
 * should branch before calling.
 */
export function formatAlleleFrequency(af: number | null): string {
  if (af == null) return "—"
  if (af === 0) return "0"
  if (af < 0.0001) return af.toExponential(2)
  return af.toFixed(4)
}
