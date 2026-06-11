/** PRS gauge card for Traits & Personality module (P3-64).
 *
 * Reuses the semicircular gauge pattern from CancerPRSGaugeCard but adds
 * a permanent "Research Use Only" banner and the ★★☆☆ evidence cap.
 * Includes ancestry mismatch warning and SNP coverage stats.
 */

import { cn } from "@/lib/utils"
import type { TraitsPRS } from "@/types/traits"
import EvidenceStars from "@/components/ui/EvidenceStars"
import { AlertTriangle, FlaskConical } from "lucide-react"

interface TraitsPRSGaugeCardProps {
  prs: TraitsPRS
}

/** Semicircular gauge with percentile needle and CI shaded arc. */
function GaugeSVG({
  percentile,
  ciLower,
  ciUpper,
}: {
  percentile: number
  ciLower: number | null
  ciUpper: number | null
}) {
  const cx = 100
  const cy = 90
  const r = 70

  const pctToAngle = (pct: number) => Math.PI - (pct / 100) * Math.PI

  const arcPath = (startPct: number, endPct: number) => {
    const startAngle = pctToAngle(startPct)
    const endAngle = pctToAngle(endPct)
    const x1 = cx + r * Math.cos(startAngle)
    const y1 = cy - r * Math.sin(startAngle)
    const x2 = cx + r * Math.cos(endAngle)
    const y2 = cy - r * Math.sin(endAngle)
    const largeArc = Math.abs(endPct - startPct) > 50 ? 1 : 0
    return `M ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2}`
  }

  const needleAngle = pctToAngle(percentile)
  const needleX = cx + (r - 10) * Math.cos(needleAngle)
  const needleY = cy - (r - 10) * Math.sin(needleAngle)

  const clampedLower = Math.max(0, ciLower ?? percentile)
  const clampedUpper = Math.min(100, ciUpper ?? percentile)

  return (
    <svg viewBox="0 0 200 110" className="w-full max-w-[200px] mx-auto" aria-hidden="true">
      {/* Background arc */}
      <path
        d={arcPath(0, 100)}
        fill="none"
        stroke="currentColor"
        strokeWidth="12"
        className="text-muted/30"
        strokeLinecap="round"
      />

      {/* CI shaded arc */}
      {ciLower != null && ciUpper != null && (
        <path
          d={arcPath(clampedLower, clampedUpper)}
          fill="none"
          stroke="currentColor"
          strokeWidth="12"
          className="text-primary/30"
          strokeLinecap="round"
        />
      )}

      {/* Needle */}
      <line
        x1={cx}
        y1={cy}
        x2={needleX}
        y2={needleY}
        stroke="currentColor"
        strokeWidth="2.5"
        className="text-primary"
        strokeLinecap="round"
      />
      <circle cx={cx} cy={cy} r="4" fill="currentColor" className="text-primary" />

      {/* Labels */}
      <text x="20" y="105" className="fill-muted-foreground text-[10px]">0%</text>
      <text x="165" y="105" className="fill-muted-foreground text-[10px]">100%</text>
    </svg>
  )
}

export default function TraitsPRSGaugeCard({ prs }: TraitsPRSGaugeCardProps) {
  const coveragePct = Math.round(prs.coverage_fraction * 100)

  return (
    <article
      className={cn(
        "rounded-lg border bg-card p-4",
        prs.ancestry_mismatch && "border-amber-300 dark:border-amber-700",
      )}
      aria-label={`${prs.name} polygenic risk score`}
      data-testid="traits-prs-card"
    >
      {/* Research Use Only banner */}
      <div
        className="rounded-md bg-violet-50 dark:bg-violet-950/30 border border-violet-200 dark:border-violet-800 px-3 py-2 mb-3"
        data-testid="research-use-banner"
      >
        <div className="flex items-center gap-2">
          <FlaskConical className="h-4 w-4 text-violet-600 dark:text-violet-400 shrink-0" aria-hidden="true" />
          <span className="text-xs font-medium text-violet-800 dark:text-violet-300">
            Research Use Only — not for clinical decision-making
          </span>
        </div>
      </div>

      {/* Header: trait name + evidence stars */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <h3 className="font-semibold text-foreground text-sm">{prs.name}</h3>
        <EvidenceStars level={prs.evidence_level} />
      </div>

      {/* Gauge visualization */}
      {prs.is_sufficient && prs.percentile != null ? (
        <div className="mb-3">
          <GaugeSVG
            percentile={prs.percentile}
            ciLower={prs.bootstrap_ci_lower}
            ciUpper={prs.bootstrap_ci_upper}
          />
          <div className="text-center mt-1">
            <p className="text-lg font-bold text-foreground">
              {Math.round(prs.percentile)}
              <span className="text-sm font-normal text-muted-foreground">th percentile</span>
            </p>
            {prs.bootstrap_ci_lower != null && prs.bootstrap_ci_upper != null && (
              <p className="text-xs text-muted-foreground">
                95% CI: {Math.round(prs.bootstrap_ci_lower)}–{Math.round(prs.bootstrap_ci_upper)}th
              </p>
            )}
          </div>
        </div>
      ) : !prs.calibrated ? (
        <div
          className="flex items-center justify-center py-6 mb-3"
          data-testid="traits-prs-uncalibrated"
        >
          <p className="text-sm text-muted-foreground italic text-center px-2">
            Percentile not reported — reference distribution unavailable
            (uncalibrated)
          </p>
        </div>
      ) : (
        <div className="flex items-center justify-center py-6 mb-3">
          <p className="text-sm text-muted-foreground italic">
            Insufficient SNP coverage ({coveragePct}%)
          </p>
        </div>
      )}

      {/* Ancestry mismatch warning */}
      {prs.ancestry_mismatch && prs.ancestry_warning_text && (
        <div
          className="rounded-md bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 p-2.5 mb-3"
          data-testid="ancestry-mismatch-warning"
        >
          <div className="flex items-start gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" aria-hidden="true" />
            <p className="text-xs text-amber-800 dark:text-amber-300">
              {prs.ancestry_warning_text}
            </p>
          </div>
        </div>
      )}

      {/* Stats footer */}
      <div className="flex items-center justify-between gap-2 pt-2 border-t border-border/50">
        <span className="text-xs text-muted-foreground">
          {prs.source_study} ({prs.source_ancestry})
        </span>
        <span className="text-xs text-muted-foreground">
          {prs.snps_used}/{prs.snps_total} SNPs ({coveragePct}%)
        </span>
      </div>
    </article>
  )
}
