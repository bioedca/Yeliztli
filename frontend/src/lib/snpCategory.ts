/**
 * Shared per-SNP category rendering for the categorical module detail panels
 * (fitness, gene_health, methylation, nutrigenomics, skin, traits).
 *
 * Pathway *levels* are Elevated / Moderate / Standard, but a per-SNP *category*
 * can additionally be "Indeterminate" — a runtime-only category for a
 * strand-ambiguous palindromic (A/T or C/G) homozygote whose strand, and
 * therefore its curated category, cannot be resolved from the array
 * (#170 / #269). The call is withheld from pathway aggregation, so it must
 * render as a neutral (slate) category rather than fall back to the green
 * "Standard" colour, which would read as a confidently-clear result.
 */

import { PATHWAY_LEVEL_COLORS, type PathwayLevel } from "@/lib/pathwayLevel"

export type SnpCategory = PathwayLevel | "Indeterminate"

/** Text colour for a per-SNP category badge. */
export const SNP_CATEGORY_COLORS: Record<SnpCategory, string> = {
  Elevated: PATHWAY_LEVEL_COLORS.Elevated.color,
  Moderate: PATHWAY_LEVEL_COLORS.Moderate.color,
  Standard: PATHWAY_LEVEL_COLORS.Standard.color,
  Indeterminate: "text-slate-600 dark:text-slate-400",
}

/** Indicator-dot colour for a per-SNP category. */
export const SNP_CATEGORY_DOT: Record<SnpCategory, string> = {
  Elevated: PATHWAY_LEVEL_COLORS.Elevated.dot,
  Moderate: PATHWAY_LEVEL_COLORS.Moderate.dot,
  Standard: PATHWAY_LEVEL_COLORS.Standard.dot,
  Indeterminate: "bg-slate-400",
}
