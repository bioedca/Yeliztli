/** Pathway card for Gene Sleep module (P3-50).
 *
 * Displays a single sleep pathway with its categorical level
 * (Elevated / Moderate / Standard), evidence stars, SNP coverage,
 * and contextual description. Click to expand detail view.
 */

import { cn } from "@/lib/utils"
import type { PathwaySummary } from "@/types/sleep"
import { PATHWAY_LEVEL_CONFIG as LEVEL_CONFIG } from "@/lib/pathwayLevel"
import { pathwayCoverageCaveat, pathwayLevelDisplayLabel } from "@/lib/pathwayCoverage"
import EvidenceStars from "@/components/ui/EvidenceStars"
import { ChevronRight, Info } from "lucide-react"

interface PathwayCardProps {
  pathway: PathwaySummary
  onClick: () => void
  selected?: boolean
}

const PATHWAY_DESCRIPTIONS: Record<string, string> = {
  caffeine_sleep:
    "Caffeine metabolism rate, sensitivity to sleep disruption, and optimal intake timing.",
  sleep_quality:
    "Sleep depth, duration needs, and restorative sleep efficiency.",
  sleep_disorders:
    "Genetic susceptibility to insomnia, restless legs syndrome, and narcolepsy proxies.",
}

export default function PathwayCard({ pathway, onClick, selected }: PathwayCardProps) {
  const config = LEVEL_CONFIG[pathway.level] || LEVEL_CONFIG.Standard
  const description = PATHWAY_DESCRIPTIONS[pathway.pathway_id] || ""
  const levelLabel = pathwayLevelDisplayLabel(pathway, config.label)
  const coverageCaveat = pathwayCoverageCaveat(pathway)

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
      aria-label={`${pathway.pathway_name} — ${levelLabel}`}
      data-selected={selected || undefined}
    >
      {/* Header: pathway name + level badge */}
      <div className="flex items-start justify-between gap-2 mb-2">
        <h3 className="font-semibold text-foreground">{pathway.pathway_name}</h3>
        <span
          className={cn(
            "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
            config.badge,
          )}
        >
          {levelLabel}
        </span>
      </div>

      {coverageCaveat && (
        <p
          className={cn(
            "mb-3 inline-flex items-start gap-1.5 rounded-md px-2 py-1 text-xs",
            "bg-muted text-muted-foreground",
          )}
          data-testid="pathway-coverage-caveat"
        >
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>{coverageCaveat}</span>
        </p>
      )}

      {/* Description */}
      {description && (
        <p className="text-sm text-muted-foreground mb-3">{description}</p>
      )}

      {/* Footer: evidence stars + SNP coverage + expand hint */}
      <div className="flex items-center justify-between gap-2 pt-2 border-t border-border/50">
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
