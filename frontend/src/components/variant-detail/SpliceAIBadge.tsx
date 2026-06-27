/** SpliceAI splice-effect prediction badge (SW-F2).
 *
 * Renders the context-only SpliceAI summary for a variant: the delta score
 * (0–1), its tier at the 0.2 / 0.5 / 0.8 operating points, the dominant splice
 * event (acceptor/donor gain/loss) and where it is predicted relative to the
 * variant. SpliceAI is an in-silico PREDICTION, not a functional assay — and is
 * explicitly NOT ACMG evidence. Renders nothing when there is no prediction (or
 * the optional BYO DB is absent). */

import { Scissors } from "lucide-react"
import type { SpliceAIBadge } from "@/types/variant-detail"
import { cn } from "@/lib/utils"

/** Tier → display label + tone. The label carries the exact operating point so
 * the colour need only separate "predicted splice-altering" (amber) from a weak
 * signal (sky); the Δ score below gives the precise value. */
const TIER_META: Record<string, { label: string; cls: string }> = {
  high_confidence: {
    label: "High-confidence (Δ ≥ 0.8)",
    cls: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  },
  likely: {
    label: "Likely (Δ ≥ 0.5)",
    cls: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  },
  possible: {
    label: "Possible (Δ ≥ 0.2)",
    cls: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  },
  none: {
    label: "No prediction",
    cls: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  },
  unknown: {
    label: "Unknown",
    cls: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  },
}

/** Render a delta position as a human-readable location relative to the variant
 * (positive = downstream, negative = upstream — per the SpliceAI convention). */
function formatDeltaPosition(dp: number | null): string | null {
  if (dp == null) return null
  if (dp === 0) return "at the variant"
  return `${Math.abs(dp)} nt ${dp > 0 ? "downstream" : "upstream"}`
}

export default function SpliceAIBadgeCard({ badge }: { badge: SpliceAIBadge | null | undefined }) {
  if (!badge) return null

  const tier = TIER_META[badge.tier] ?? TIER_META.unknown
  const deltaPosition = formatDeltaPosition(badge.top_delta_position)

  return (
    <div
      className="rounded-md border border-violet-200 bg-violet-50 p-3 dark:border-violet-900 dark:bg-violet-950/30"
      data-testid="spliceai-badge"
    >
      <div className="flex items-start gap-2">
        <Scissors className="mt-0.5 h-4 w-4 shrink-0 text-violet-600 dark:text-violet-400" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold text-violet-900 dark:text-violet-200">
              SpliceAI splice prediction
            </p>
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                tier.cls,
              )}
            >
              {tier.label}
            </span>
          </div>
          <dl className="mt-1.5 space-y-0.5 text-xs text-violet-800 dark:text-violet-300">
            {badge.ds_max != null && (
              <div className="flex justify-between gap-2">
                <dt className="text-violet-700/80 dark:text-violet-400/80">Delta score</dt>
                <dd className="font-mono">{badge.ds_max.toFixed(2)}</dd>
              </div>
            )}
            {badge.top_mode_label && (
              <div className="flex justify-between gap-2">
                <dt className="text-violet-700/80 dark:text-violet-400/80">Predicted event</dt>
                <dd>{badge.top_mode_label}</dd>
              </div>
            )}
            {deltaPosition && (
              <div className="flex justify-between gap-2">
                <dt className="text-violet-700/80 dark:text-violet-400/80">Location</dt>
                <dd>{deltaPosition}</dd>
              </div>
            )}
            {badge.symbol && (
              <div className="flex justify-between gap-2">
                <dt className="text-violet-700/80 dark:text-violet-400/80">Gene</dt>
                <dd className="font-mono">{badge.symbol}</dd>
              </div>
            )}
          </dl>
          <p className="mt-2 text-[10px] italic leading-snug text-violet-700/80 dark:text-violet-400/80">
            In-silico prediction, not a functional assay — confirm with RNA/functional testing.
            Context only; not ACMG evidence (no PVS1/PP3/PS3 uplift).
          </p>
        </div>
      </div>
    </div>
  )
}
