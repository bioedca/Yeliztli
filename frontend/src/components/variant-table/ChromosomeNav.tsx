/** Chromosome anchor navigation bar (P1-15b).
 *
 * Horizontal bar with buttons for chr1–22, X, Y, MT. Clicking an anchor
 * jumps to the first variant on that chromosome via the cursor API.
 * Shows a visual indicator of the current chromosome and variant counts.
 */

import { useMemo } from "react"
import { CHROMOSOMES, type ChromosomeSummary } from "@/types/variants"
import { cn } from "@/lib/utils"

interface ChromosomeNavProps {
  /** Per-chromosome variant counts from the API. */
  chromosomeCounts: ChromosomeSummary[] | undefined
  /** Whether chromosome count data is loading. */
  isLoading: boolean
  /** Currently active/visible chromosome (derived from loaded data). */
  activeChrom: string | null
  /** Callback when a chromosome button is clicked. */
  onJumpToChrom: (chrom: string) => void
  /**
   * When set, every jump button is disabled and the text is rendered as a
   * visible note beside the bar (a disabled button leaves the tab order, so a
   * hover-only title would hide the reason from keyboard and touch users) —
   * used while a search term is active, because a search spans the whole
   * sample and a jump would only take effect once the term is cleared (#2058).
   */
  disabledReason?: string
}

export default function ChromosomeNav({
  chromosomeCounts,
  isLoading,
  activeChrom,
  onJumpToChrom,
  disabledReason,
}: ChromosomeNavProps) {
  // Build a lookup map: chrom -> count
  const countMap = useMemo(() => {
    const map = new Map<string, number>()
    if (chromosomeCounts) {
      for (const { chrom, count } of chromosomeCounts) {
        map.set(chrom, count)
      }
    }
    return map
  }, [chromosomeCounts])

  // Find max count for relative sizing of count indicators
  const maxCount = useMemo(() => {
    if (!chromosomeCounts?.length) return 0
    return Math.max(...chromosomeCounts.map((c) => c.count))
  }, [chromosomeCounts])

  if (isLoading) {
    return (
      <div
        className="flex items-center gap-1 px-4 py-1.5 border-b border-border bg-card overflow-x-auto" tabIndex={0}
        aria-label="Chromosome navigation"
        role="toolbar"
      >
        <span className="text-xs text-muted-foreground mr-2 shrink-0">Chr</span>
        {CHROMOSOMES.map((chrom) => (
          <span
            key={chrom}
            className="h-7 w-7 rounded bg-muted animate-pulse shrink-0 inline-block"
          />
        ))}
      </div>
    )
  }

  return (
    <div
      className="flex items-center gap-1 px-4 py-1.5 border-b border-border bg-card overflow-x-auto" tabIndex={0}
      aria-label="Chromosome navigation"
      aria-describedby={disabledReason ? "chromosome-nav-paused" : undefined}
      role="toolbar"
    >
      <span className="text-xs text-muted-foreground mr-2 shrink-0 font-medium">Chr</span>
      {disabledReason && (
        <span
          id="chromosome-nav-paused"
          role="status"
          data-testid="chromosome-nav-paused"
          className="text-xs text-muted-foreground mr-2 shrink-0"
        >
          {disabledReason}
        </span>
      )}
      {CHROMOSOMES.map((chrom) => {
        const count = countMap.get(chrom) ?? 0
        const hasData = count > 0
        const canJump = hasData && !disabledReason
        const isActive = activeChrom === chrom
        // Relative intensity: opacity scales with count proportion
        const intensity = hasData && maxCount > 0 ? Math.max(0.15, count / maxCount) : 0

        return (
          <button
            key={chrom}
            type="button"
            onClick={() => canJump && onJumpToChrom(chrom)}
            disabled={!canJump}
            title={
              hasData && disabledReason
                ? disabledReason
                : hasData
                  ? `Chromosome ${chrom}: ${count.toLocaleString()} variants`
                  : `Chromosome ${chrom}: no variants`
            }
            aria-label={`Jump to chromosome ${chrom}${hasData ? `, ${count.toLocaleString()} variants` : ""}`}
            aria-current={isActive ? "location" : undefined}
            className={cn(
              "relative flex flex-col items-center justify-center shrink-0",
              "min-w-[28px] h-8 px-1 rounded text-xs font-mono transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
              isActive && hasData
                ? "bg-primary text-primary-foreground font-semibold shadow-sm"
                : hasData
                  ? "bg-background border border-input text-foreground hover:bg-accent hover:text-accent-foreground"
                  : "bg-muted/50 text-muted-foreground/70 cursor-not-allowed border border-transparent",
            )}
          >
            <span>{chrom}</span>
            {/* Variant density indicator bar */}
            {hasData && !isActive && (
              <span
                className="absolute bottom-0.5 left-1/2 -translate-x-1/2 h-[2px] rounded-full bg-primary"
                style={{ width: `${Math.max(20, intensity * 100)}%` }}
                aria-hidden="true"
              />
            )}
          </button>
        )
      })}
    </div>
  )
}
