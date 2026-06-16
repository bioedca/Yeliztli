/** Ancestry inference summary card (P3-27, AMv2 Step 5).
 *
 * Shows the top inferred population, coverage stats, confidence badge,
 * missing AIM rate quality indicator, and evidence level.
 */

import { cn } from "@/lib/utils"
import { formatNumber } from "@/lib/format"
import EvidenceStars from "@/components/ui/EvidenceStars"
import type { AncestryFindingResponse } from "@/types/ancestry"
import { POPULATION_COLORS, POPULATION_LABELS } from "./constants"

interface AncestryResultCardProps {
  finding: AncestryFindingResponse
}

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100)
  if (pct === 0) return null
  const isHigh = pct >= 90
  const color = isHigh
    ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
    : "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400"
  const label = isHigh ? "High confidence" : "Moderate confidence"
  return (
    <span
      className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", color)}
      data-testid="confidence-badge"
    >
      {label}
    </span>
  )
}

function MissingAimIndicator({ rate }: { rate: number }) {
  if (rate <= 0) return null
  const pct = Math.round(rate * 100)
  const isHigh = pct > 20
  return (
    <span
      className={cn(
        "text-xs",
        isHigh
          ? "text-amber-700 dark:text-amber-400 font-medium"
          : "text-muted-foreground",
      )}
      data-testid="missing-aim-indicator"
    >
      {pct}% AIMs missing
    </span>
  )
}

export default function AncestryResultCard({ finding }: AncestryResultCardProps) {
  const topLabel = POPULATION_LABELS[finding.top_population] ?? finding.top_population
  const coveragePct = Math.round(finding.coverage_fraction * 100)

  return (
    <div
      className="rounded-lg border bg-card p-5"
      data-testid="ancestry-result-card"
    >
      <div className="flex items-start gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 mb-2">
            <h3 className="text-lg font-semibold text-foreground">
              Inferred Ancestry
            </h3>
            <span
              className={cn(
                "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
                "bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-300",
              )}
              data-testid="top-population-badge"
            >
              {topLabel}
            </span>
            <ConfidenceBadge confidence={finding.confidence} />
          </div>

          <p className="text-sm text-muted-foreground mb-3">
            {finding.finding_text}
          </p>

          <div className="flex items-center gap-4 text-xs text-muted-foreground flex-wrap">
            <span>
              {formatNumber(finding.snps_used)} / {formatNumber(finding.snps_total)} AIMs used ({coveragePct}%)
            </span>
            <span className="flex items-center gap-1">
              Evidence: <EvidenceStars level={finding.evidence_level} />
            </span>
            <MissingAimIndicator rate={finding.missing_aim_rate} />
            {!finding.is_sufficient && (
              <span className="text-amber-700 dark:text-amber-400 font-medium">
                Low coverage — results may be unreliable
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Population ranking */}
      {finding.population_ranking.length > 0 && (
        <div className="mt-4 pt-3 border-t">
          <p className="text-xs font-medium text-muted-foreground">Population Ranking</p>
          {/* The value is the distance to each population's PCA centroid; the list
              is sorted best-first, so the numbers increase down the list. Without
              this caption a reader naturally misreads "bigger number = stronger
              match", which is backwards (#532). */}
          <p className="text-[11px] text-muted-foreground mb-2">
            Distance to population centroid · lower is closer
          </p>
          <div className="space-y-1">
            {finding.population_ranking.map((pr, index) => {
              const label = POPULATION_LABELS[pr.population] ?? pr.population
              const color = POPULATION_COLORS[pr.population] ?? "#94A3B8"
              const rank = index + 1
              return (
                <div
                  key={pr.population}
                  className="flex items-center justify-between text-xs"
                >
                  <span className="flex items-center gap-2 min-w-0">
                    <span className="font-mono tabular-nums text-muted-foreground w-5 shrink-0">
                      #{rank}
                    </span>
                    <span
                      className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ backgroundColor: color }}
                    />
                    <span className="text-foreground truncate">{label}</span>
                  </span>
                  <span
                    className="text-muted-foreground font-mono shrink-0"
                    title={`${label}: distance ${pr.distance.toFixed(4)} to the population centroid (lower is closer)`}
                    aria-label={`${label}: rank ${rank}, distance ${pr.distance.toFixed(4)} (lower is closer)`}
                  >
                    {pr.distance.toFixed(4)}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
