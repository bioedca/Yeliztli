/**
 * Single source of truth for categorical pathway-level (Elevated / Moderate /
 * Standard) colours, so a level's badge is identical on every surface — the seven
 * module PathwayCards, the methylation PathwayScoreBar, and the All Findings page
 * (#613).
 *
 * Previously each PathwayCard duplicated this map and `FindingsExplorer` inlined a
 * *different* one (Elevated→red, Moderate→amber, Standard→green), so the same
 * amber badge meant "Elevated" in a module view but "Moderate" in All Findings —
 * the severity→colour scale flipped between surfaces.
 *
 * The hue scale (Elevated→amber, Moderate→blue, Standard→emerald) matches the
 * per-SNP category colours in `snpCategory.ts`, so a given level reads the same
 * whether it is shown as a SNP category or a pathway level. Light text tokens are
 * -700/-800 (and dark -300/-400), clearing WCAG AA contrast at the small badge
 * size (cf. #573 / #678).
 */

export type PathwayLevel = "Elevated" | "Moderate" | "Standard"

export interface PathwayLevelColors {
  /** Heading / value text colour. */
  color: string
  /** Subtle tint background (cards). */
  bg: string
  /** Card border. */
  border: string
  /** Pill badge (background + text) — the surface where the cross-view bug lived. */
  badge: string
  /** Progress-bar fill (methylation PathwayScoreBar). */
  bar: string
  /** Small indicator dot used by per-SNP category rows. */
  dot: string
  /** SVG node fill/stroke/text classes (methylation PathwayFlowDiagram). */
  svg: { bg: string; border: string; text: string }
}

export const PATHWAY_LEVEL_COLORS: Record<PathwayLevel, PathwayLevelColors> = {
  Elevated: {
    color: "text-amber-700 dark:text-amber-400",
    bg: "bg-amber-50 dark:bg-amber-950/30",
    border: "border-amber-200 dark:border-amber-800",
    badge: "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300",
    bar: "bg-amber-400 dark:bg-amber-500",
    dot: "bg-amber-500",
    svg: {
      bg: "fill-amber-100 dark:fill-amber-950",
      border: "stroke-amber-400 dark:stroke-amber-600",
      text: "fill-amber-800 dark:fill-amber-300",
    },
  },
  Moderate: {
    color: "text-blue-700 dark:text-blue-400",
    bg: "bg-blue-50 dark:bg-blue-950/30",
    border: "border-blue-200 dark:border-blue-800",
    badge: "bg-blue-100 text-blue-800 dark:bg-blue-900/50 dark:text-blue-300",
    bar: "bg-blue-400 dark:bg-blue-500",
    dot: "bg-blue-500",
    svg: {
      bg: "fill-blue-100 dark:fill-blue-950",
      border: "stroke-blue-400 dark:stroke-blue-600",
      text: "fill-blue-800 dark:fill-blue-300",
    },
  },
  Standard: {
    color: "text-emerald-700 dark:text-emerald-400",
    bg: "bg-emerald-50 dark:bg-emerald-950/30",
    border: "border-emerald-200 dark:border-emerald-800",
    badge: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-300",
    bar: "bg-emerald-400 dark:bg-emerald-500",
    dot: "bg-emerald-500",
    svg: {
      bg: "fill-emerald-100 dark:fill-emerald-950",
      border: "stroke-emerald-400 dark:stroke-emerald-600",
      text: "fill-emerald-800 dark:fill-emerald-300",
    },
  },
}

/**
 * Ready-made level config (default labels + colours) for the module PathwayCards
 * and the methylation PathwayScoreBar. Components that need different labels (e.g.
 * nutrigenomics' "Elevated Consideration") build their own from
 * `PATHWAY_LEVEL_COLORS` instead.
 */
export const PATHWAY_LEVEL_CONFIG: Record<
  PathwayLevel,
  PathwayLevelColors & { label: PathwayLevel }
> = {
  Elevated: { label: "Elevated", ...PATHWAY_LEVEL_COLORS.Elevated },
  Moderate: { label: "Moderate", ...PATHWAY_LEVEL_COLORS.Moderate },
  Standard: { label: "Standard", ...PATHWAY_LEVEL_COLORS.Standard },
}

/**
 * Pill-badge class for a pathway level, with a safe fallback (Standard) for an
 * unrecognised value so a level never renders unstyled.
 */
export function pathwayLevelBadge(level: string): string {
  return (PATHWAY_LEVEL_COLORS[level as PathwayLevel] ?? PATHWAY_LEVEL_COLORS.Standard).badge
}

/**
 * SVG node fill/stroke/text classes for a pathway level (methylation
 * PathwayFlowDiagram), with the same Standard fallback as `pathwayLevelBadge`.
 */
export function pathwayLevelSvg(level: string): PathwayLevelColors["svg"] {
  return (PATHWAY_LEVEL_COLORS[level as PathwayLevel] ?? PATHWAY_LEVEL_COLORS.Standard).svg
}
