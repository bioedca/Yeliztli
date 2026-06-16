/** Per-pathway score bar for MTHFR & Methylation (P3-53).
 *
 * Horizontal bar showing the pathway level (Elevated / Moderate / Standard)
 * with evidence stars, SNP coverage, and additive promotion indicator.
 * Clickable to open the detail panel.
 */

import { cn } from "@/lib/utils"
import type { PathwaySummary, PathwayLevel } from "@/types/methylation"
import { PATHWAY_LEVEL_CONFIG as LEVEL_CONFIG } from "@/lib/pathwayLevel"
import EvidenceStars from "@/components/ui/EvidenceStars"
import { ChevronRight, TrendingUp } from "lucide-react"

interface PathwayScoreBarProps {
  pathway: PathwaySummary
  onClick: () => void
  selected?: boolean
}

const BAR_WIDTH: Record<PathwayLevel, string> = {
  Elevated: "w-full",
  Moderate: "w-2/3",
  Standard: "w-1/3",
}

export default function PathwayScoreBar({ pathway, onClick, selected }: PathwayScoreBarProps) {
  const config = LEVEL_CONFIG[pathway.level] || LEVEL_CONFIG.Standard
  const barWidth = BAR_WIDTH[pathway.level] || BAR_WIDTH.Standard

  return (
    <button
      type="button"
      className={cn(
        "w-full text-left rounded-lg border p-4 cursor-pointer transition-all",
        "hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        config.bg,
        config.border,
        selected && "ring-2 ring-primary",
      )}
      onClick={onClick}
      aria-label={`${pathway.pathway_name} — ${config.label}`}
      data-selected={selected || undefined}
    >
      {/* Header: pathway name + level badge */}
      <div className="flex items-start justify-between gap-2 mb-2">
        <h3 className="font-semibold text-foreground">{pathway.pathway_name}</h3>
        <div className="flex items-center gap-1.5">
          {pathway.additive_promoted && (
            <span
              className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300"
              title="Level promoted by additive scoring (≥3 moderate SNPs with ★★ evidence)"
            >
              <TrendingUp className="h-3 w-3" aria-hidden="true" />
              Promoted
            </span>
          )}
          <span
            className={cn(
              "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
              config.badge,
            )}
          >
            {config.label}
          </span>
        </div>
      </div>

      {/* Score bar */}
      <div className="mb-3">
        <div className="h-2.5 w-full rounded-full bg-muted/60">
          <div
            className={cn("h-2.5 rounded-full transition-all duration-500", config.bar, barWidth)}
          />
        </div>
      </div>

      {/* Footer: evidence stars + SNP coverage + expand hint */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          <EvidenceStars level={pathway.evidence_level} />
          <span className="text-xs text-muted-foreground">
            {pathway.called_snps}/{pathway.total_snps} SNPs called
          </span>
        </div>
        <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" aria-hidden="true" />
      </div>
    </button>
  )
}
