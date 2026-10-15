/** Scoring progress, failure and a manual re-score for route-only modules (#2118). */

import { AlertCircle, Loader2, RefreshCw } from "lucide-react"
import type { ScoringState } from "@/types/route-scoring"

interface ScoringStatusProps {
  scoringState: ScoringState | undefined
  isScoring: boolean
  error: unknown
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
  onRescore,
}: ScoringStatusProps) {
  return (
    <div className="mb-6 space-y-2" data-testid="scoring-status">
      <div className="flex flex-wrap items-center gap-3">
        <div role="status" aria-live="polite" className="text-sm text-muted-foreground">
          {isScoring && (
            <span className="inline-flex items-center gap-1.5">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              {scoringMessage(scoringState)}
            </span>
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
