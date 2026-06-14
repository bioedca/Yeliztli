/** Pathway consideration card for nutrigenomics (P3-11).
 *
 * Displays a single nutrient pathway with its categorical level
 * (Elevated / Moderate / Standard), evidence stars, SNP coverage,
 * and plain-language description. Click to expand detail view.
 */

import { cn } from "@/lib/utils"
import type { PathwaySummary } from "@/types/nutrigenomics"
import { PATHWAY_LEVEL_COLORS } from "@/lib/pathwayLevel"
import EvidenceStars from "@/components/ui/EvidenceStars"
import { ChevronRight } from "lucide-react"

interface PathwayCardProps {
  pathway: PathwaySummary
  onClick: () => void
  selected?: boolean
}

// Nutrigenomics keeps its own "… Consideration" labels but sources colours from
// the shared pathway-level map so badges stay consistent app-wide (#613).
const LEVEL_CONFIG = {
  Elevated: { label: "Elevated Consideration", ...PATHWAY_LEVEL_COLORS.Elevated },
  Moderate: { label: "Moderate Consideration", ...PATHWAY_LEVEL_COLORS.Moderate },
  Standard: { label: "Standard", ...PATHWAY_LEVEL_COLORS.Standard },
}

const PATHWAY_DESCRIPTIONS: Record<string, string> = {
  folate_metabolism:
    "Folate (vitamin B9) is essential for DNA synthesis, methylation, and cell division.",
  vitamin_d:
    "Vitamin D supports bone health, immune function, and calcium absorption.",
  vitamin_b12:
    "Vitamin B12 is critical for nerve function, red blood cell formation, and DNA synthesis.",
  omega_3:
    "Omega-3 fatty acids support cardiovascular health and inflammation regulation.",
  iron:
    "Iron metabolism governs oxygen transport and energy production.",
  lactose:
    "Lactose tolerance determines the ability to digest dairy sugars in adulthood.",
}

export default function PathwayCard({ pathway, onClick, selected }: PathwayCardProps) {
  const config = LEVEL_CONFIG[pathway.level] || LEVEL_CONFIG.Standard
  const description = PATHWAY_DESCRIPTIONS[pathway.pathway_id] || ""

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
        <span
          className={cn(
            "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
            config.badge,
          )}
        >
          {config.label}
        </span>
      </div>

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
