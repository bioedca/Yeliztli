/** Per-gene metabolizer phenotype card with three-state indicator (P3-06). */

import { cn } from "@/lib/utils"
import type { GeneSummary, CallConfidence } from "@/types/pharmacogenomics"
import { CheckCircle2, AlertTriangle, XCircle } from "lucide-react"

interface MetabolizerCardProps {
  gene: GeneSummary
}

const CONFIDENCE_CONFIG: Record<
  CallConfidence,
  { icon: typeof CheckCircle2; label: string; color: string }
> = {
  Complete: {
    icon: CheckCircle2,
    label: "Complete",
    color: "text-emerald-700 dark:text-emerald-400",
  },
  Partial: {
    icon: AlertTriangle,
    label: "Partial",
    color: "text-amber-700 dark:text-amber-400",
  },
  Insufficient: {
    icon: XCircle,
    label: "Insufficient",
    color: "text-red-700 dark:text-red-400",
  },
}

const RESULT_CONFIG = {
  routine: {
    card: "border-emerald-200 bg-emerald-50/60 dark:border-emerald-900/70 dark:bg-emerald-950/20",
    phenotype: "font-medium text-emerald-800 dark:text-emerald-300",
  },
  review: {
    card: "border-amber-300 bg-amber-50/70 dark:border-amber-800 dark:bg-amber-950/20",
    phenotype: "font-medium text-amber-900 dark:text-amber-200",
  },
  unavailable: {
    card: "border-red-200 bg-red-50/60 dark:border-red-900/70 dark:bg-red-950/20",
    phenotype: "text-red-800 dark:text-red-300",
  },
} as const

function resultTone(phenotype: string | null): keyof typeof RESULT_CONFIG {
  if (!phenotype) return "unavailable"
  const normalized = phenotype.toLowerCase()
  if (normalized === "normal metabolizer" || normalized === "normal function") {
    return "routine"
  }
  return "review"
}

function EvidenceStars({ level }: { level: number | null }) {
  if (level == null) return null
  const stars = Math.max(0, Math.min(4, level))
  return (
    <span className="text-xs text-muted-foreground" role="img" aria-label={`${stars} of 4 stars evidence`}>
      {"★".repeat(stars)}
      {"☆".repeat(4 - stars)}
    </span>
  )
}

export default function MetabolizerCard({ gene }: MetabolizerCardProps) {
  const confidence = gene.call_confidence
  const config = confidence ? CONFIDENCE_CONFIG[confidence] : null
  const ConfidenceIcon = config?.icon
  const resultConfig = RESULT_CONFIG[resultTone(gene.phenotype)]

  return (
    <article
      className={cn("rounded-lg border p-4 transition-colors", resultConfig.card)}
      aria-label={`${gene.gene} metabolizer status`}
    >
      {/* Header: gene name + confidence badge */}
      <div className="flex items-center justify-between gap-2 mb-2">
        <h3 className="font-semibold text-sm text-foreground">{gene.gene}</h3>
        {config && ConfidenceIcon && (
          <span
            className={cn("flex items-center gap-1 text-xs font-medium", config.color)}
            title={gene.confidence_note ?? config.label}
          >
            <ConfidenceIcon className="h-3.5 w-3.5" aria-hidden="true" />
            {config.label}
          </span>
        )}
      </div>

      {/* Diplotype */}
      {gene.diplotype && (
        <p className="text-sm font-mono text-foreground mb-1">{gene.diplotype}</p>
      )}

      {/* Phenotype (metabolizer status) */}
      {gene.phenotype ? (
        <p className={cn("text-sm mb-2", resultConfig.phenotype)}>{gene.phenotype}</p>
      ) : (
        <p className={cn("text-sm italic mb-2", resultConfig.phenotype)}>
          No result available
        </p>
      )}

      {/* Footer: evidence stars + activity score */}
      <div className="flex items-center justify-between gap-2 pt-2 border-t border-border/50">
        <EvidenceStars level={gene.evidence_level} />
        {gene.activity_score != null && (
          <span className="text-xs text-muted-foreground">
            Activity: {gene.activity_score}
          </span>
        )}
      </div>

      {/* Confidence note tooltip text */}
      {gene.confidence_note && confidence === "Partial" && (
        <p className="text-xs text-amber-700 dark:text-amber-400 mt-2 italic">
          {gene.confidence_note}
        </p>
      )}

      {/* Gene-specific interpretive caveat (e.g. DPYD fatal-toxicity / absent-allele).
          Context only — mirrors the backend disclaimer; never overrides the result. */}
      {gene.gene_caveat && (
        <div
          className="mt-2 flex gap-1.5 rounded-md bg-amber-50 dark:bg-amber-950/30 p-2"
          role="note"
          aria-label={`${gene.gene} interpretation caveat`}
        >
          <AlertTriangle
            className="h-3.5 w-3.5 shrink-0 mt-0.5 text-amber-600 dark:text-amber-400"
            aria-hidden="true"
          />
          <p className="text-xs text-amber-700 dark:text-amber-300">{gene.gene_caveat}</p>
        </div>
      )}

      {/* Associated drugs */}
      {gene.drugs.length > 0 && (
        <div className="mt-2 pt-2 border-t border-border/50">
          <p className="text-xs text-muted-foreground">
            Affects: {gene.drugs.slice(0, 3).join(", ")}
            {gene.drugs.length > 3 && ` +${gene.drugs.length - 3} more`}
          </p>
        </div>
      )}
    </article>
  )
}
