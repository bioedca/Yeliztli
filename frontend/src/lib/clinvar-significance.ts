export type ClinvarSignificanceTone =
  | "pathogenic"
  | "likely-pathogenic"
  | "benign"
  | "uncertain"
  | "neutral"

type ClinvarCardConfig = {
  color: string
  bg: string
  border: string
  badge: string
}

const CARD_CONFIG: Record<ClinvarSignificanceTone, ClinvarCardConfig> = {
  pathogenic: {
    color: "text-red-700 dark:text-red-400",
    bg: "bg-red-50 dark:bg-red-950/30",
    border: "border-red-200 dark:border-red-800",
    badge: "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300",
  },
  "likely-pathogenic": {
    color: "text-orange-700 dark:text-orange-400",
    bg: "bg-orange-50 dark:bg-orange-950/30",
    border: "border-orange-200 dark:border-orange-800",
    badge: "bg-orange-100 text-orange-800 dark:bg-orange-900/50 dark:text-orange-300",
  },
  benign: {
    color: "text-green-700 dark:text-green-400",
    bg: "bg-green-50 dark:bg-green-950/30",
    border: "border-green-200 dark:border-green-800",
    badge: "bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300",
  },
  uncertain: {
    color: "text-yellow-700 dark:text-yellow-400",
    bg: "bg-yellow-50 dark:bg-yellow-950/30",
    border: "border-yellow-200 dark:border-yellow-800",
    badge: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/50 dark:text-yellow-300",
  },
  neutral: {
    color: "text-muted-foreground",
    bg: "bg-card",
    border: "border-border",
    badge: "bg-muted text-muted-foreground",
  },
}

const TEXT_CLASS: Record<ClinvarSignificanceTone, string> = {
  pathogenic: "text-red-600 dark:text-red-400",
  "likely-pathogenic": "text-orange-600 dark:text-orange-400",
  benign: "text-green-600 dark:text-green-400",
  uncertain: "text-yellow-600 dark:text-yellow-400",
  neutral: "text-muted-foreground",
}

// Raw hex by tone, for SVG/canvas contexts that can't take a Tailwind class
// (e.g. the Nightingale protein viewer). Same severity *ordering* as the class
// palettes; the hues are the protein viewer's own legend swatches, so `uncertain`
// is amber (#D97706) to match that viewer's existing "VUS" swatch — deliberately
// distinct from the badge/text palette's yellow. The #799 invariant is only that
// `uncertain` (which covers conflicting) is never the pathogenic red.
const HEX_COLOR: Record<ClinvarSignificanceTone, string> = {
  pathogenic: "#DC2626", // red-600
  "likely-pathogenic": "#EA580C", // orange-600
  benign: "#16A34A", // green-600
  uncertain: "#D97706", // amber-600 — matches the Nightingale legend's VUS swatch
  neutral: "#6B7280", // gray-500
}

function normalizeClinvarSignificance(significance: string | null | undefined): string {
  return (significance ?? "")
    .trim()
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
}

export function getClinvarSignificanceTone(
  significance: string | null | undefined,
): ClinvarSignificanceTone {
  const normalized = normalizeClinvarSignificance(significance)
  if (!normalized) return "neutral"

  const parts = normalized
    .split(/[/|;,]+/)
    .map((part) => part.trim())
    .filter(Boolean)
  const hasPathogenic = parts.includes("pathogenic")
  const hasLikelyPathogenic = parts.includes("likely pathogenic")
  const hasBenign = normalized.includes("benign")
  const hasVus = parts.includes("vus")

  if (normalized.includes("conflicting")) return "uncertain"
  if (normalized.includes("uncertain") || hasVus) return "uncertain"
  if ((hasPathogenic || hasLikelyPathogenic) && hasBenign) return "uncertain"
  if (hasPathogenic && !hasBenign) return "pathogenic"
  if (hasLikelyPathogenic && !hasBenign) return "likely-pathogenic"
  if (hasBenign) return "benign"

  return "neutral"
}

export function getClinvarSignificanceCardConfig(
  significance: string | null | undefined,
): ClinvarCardConfig {
  return CARD_CONFIG[getClinvarSignificanceTone(significance)]
}

export function getClinvarSignificanceTextClass(
  significance: string | null | undefined,
): string {
  return TEXT_CLASS[getClinvarSignificanceTone(significance)]
}

export function getClinvarSignificanceBadgeClass(
  significance: string | null | undefined,
): string {
  return getClinvarSignificanceCardConfig(significance).badge
}

export function getClinvarSignificanceHexColor(
  significance: string | null | undefined,
): string {
  return HEX_COLOR[getClinvarSignificanceTone(significance)]
}
