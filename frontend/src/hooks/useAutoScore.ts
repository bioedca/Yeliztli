import { useEffect, useRef } from "react"
import type { ScoringState } from "@/types/route-scoring"

interface AutoScoreOptions {
  sampleId: number | null
  /** The stored result has loaded and no refetch is in flight. */
  settled: boolean
  scoringState: ScoringState | undefined
  run: () => void
  /** Clears the run's pending/error state. */
  reset: () => void
}

/**
 * Score a route-only module once its stored result is known to be missing or
 * stale, instead of on every visit (#2118). A run takes minutes on a full array.
 *
 * Decides only on a settled response, so a cached state shown while it is
 * re-fetched neither starts nor skips a run. Runs at most once per sample and
 * state for this mount, so a failed run does not loop and React StrictMode's
 * double effect does not start two runs. A response without a state (an older
 * backend) is scored, as every visit was before. Switching sample on the same
 * page resets the run, so one sample's progress or failure is not shown on
 * another's.
 */
export function useAutoScore({
  sampleId,
  settled,
  scoringState,
  run,
  reset,
}: AutoScoreOptions): void {
  const shownSample = useRef(sampleId)
  useEffect(() => {
    if (shownSample.current === sampleId) return
    shownSample.current = sampleId
    reset()
  }, [sampleId, reset])

  const requested = useRef<string | null>(null)
  useEffect(() => {
    if (sampleId == null || !settled || scoringState === "current") return
    const key = `${sampleId}:${scoringState ?? "unknown"}`
    if (requested.current === key) return
    requested.current = key
    run()
  }, [sampleId, settled, scoringState, run])
}
