/** Scoring progress, failure and a manual re-score for route-only modules (#2118). */

import { AlertCircle, Loader2, RefreshCw } from "lucide-react"
import type { ScoringState } from "@/types/route-scoring"

interface ScoringStatusProps {
  scoringState: ScoringState | undefined
  isScoring: boolean
  error: unknown
  /** The stored results are from cache because the latest re-fetch failed. */
  refreshFailed: boolean
  onRescore: () => void
}

const BUTTON_CLASS =
  "inline-flex items-center gap-1.5 rounded-md border border-input bg-background px-3 py-1.5 text-sm font-medium hover:bg-accent transition-colors disabled:opacity-50 disabled:cursor-not-allowed"

function scoringMessage(scoringState: ScoringState | undefined): string {
  if (scoringState === "never") {
    return "Scoring this sample. This can take a few minutes."
  }
  if (scoringState === "stale") {
    return "This sample's inputs changed since it was last scored, so it is being re-scored. This can take a few minutes; the results below are from the previous score."
  }
  return "Re-scoring this sample. This can take a few minutes."
}

export default function ScoringStatus({
  scoringState,
  isScoring,
  error,
  refreshFailed,
  onRescore,
}: ScoringStatusProps) {
  return (
    <div className="mb-6 space-y-2" data-testid="scoring-status">
      <div className="flex flex-wrap items-center gap-3">
        <div role="status" aria-live="polite" className="text-sm text-muted-foreground">
          {isScoring ? (
            <span className="inline-flex items-center gap-1.5">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              {scoringMessage(scoringState)}
            </span>
          ) : (
            scoringState === "stale" &&
            "This sample's inputs changed since it was last scored, so the results below may be out of date. Re-score to update them."
          )}
        </div>
        <button
          type="button"
          onClick={onRescore}
          disabled={isScoring}
          className={BUTTON_CLASS}
          data-testid="rescore-btn"
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          Re-score
        </button>
      </div>
      {refreshFailed && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-md border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-3 text-sm"
        >
          <AlertCircle className="h-4 w-4 text-amber-600 mt-0.5 shrink-0" aria-hidden="true" />
          <p>Couldn't check these results for changes; showing the last ones loaded.</p>
        </div>
      )}
      {!isScoring && error != null && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-md border border-destructive/50 bg-destructive/5 p-3 text-sm"
        >
          <AlertCircle className="h-4 w-4 text-destructive mt-0.5 shrink-0" aria-hidden="true" />
          <p>
            <span className="font-medium text-destructive">Scoring failed.</span>{" "}
            {error instanceof Error ? error.message : "Please try again."}
          </p>
        </div>
      )}
    </div>
  )
}
