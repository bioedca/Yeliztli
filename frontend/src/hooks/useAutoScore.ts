import { useEffect, useRef } from "react"
import type { ScoringState } from "@/types/route-scoring"

/** Automatic runs per sample per page visit; Re-score is never limited. */
const MAX_AUTO_RUNS = 3

interface RunCallbacks {
  onSuccess: () => void
  onError: () => void
}

interface AutoScoreOptions {
  sampleId: number | null
  /** The stored result loaded, its latest fetch succeeded and none is in flight. */
  settled: boolean
  scoringState: ScoringState | undefined
  /** Starts a run; the run mutation's ``mutate``. */
  run: (variables: undefined, callbacks: RunCallbacks) => void
  /** Clears the run's pending/error state. */
  reset: () => void
}

/**
 * Score a route-only module once its stored result is known to be missing or
 * stale, instead of on every visit (#2118). A run takes minutes on a full array.
 *
 * - Decides only on a settled response and never while its own run is in
 *   flight, so a cached state shown while it is re-fetched neither starts nor
 *   skips a run, and StrictMode's double effect starts one run.
 * - After a failed run it waits for the state to change instead of retrying in
 *   a loop. A successful run lets a ``stale`` result start another run (an
 *   input that changed mid-run leaves it stale), and a ``current`` state clears
 *   the failure.
 * - Starts at most ``MAX_AUTO_RUNS`` per sample per visit, so a result that
 *   never settles cannot loop minutes-long runs.
 * - A response without a state (an older backend) is scored once per visit, as
 *   every visit was before.
 * - Switching sample on the same page resets the run and these guards, so one
 *   sample's progress or failure is not shown on another's and a sample shown
 *   again decides afresh.
 *
 * Completion is tracked through ``mutate``'s per-call callbacks rather than the
 * mutation status, which can pass through ``pending`` without a render.
 */
export function useAutoScore({
  sampleId,
  settled,
  scoringState,
  run,
  reset,
}: AutoScoreOptions): void {
  const shownSample = useRef(sampleId)
  const inFlight = useRef(false)
  // A state not to run again automatically until it changes: its run failed,
  // or the response reports no state, so a success cannot be told apart.
  const blockedFor = useRef<string | null>(null)
  const autoRuns = useRef(0)

  useEffect(() => {
    if (shownSample.current === sampleId) return
    shownSample.current = sampleId
    // reset() drops the pending run's callbacks, so clear what they would have.
    inFlight.current = false
    blockedFor.current = null
    autoRuns.current = 0
    reset()
  }, [sampleId, reset])

  // No dependency list: the decision reads refs the run's callbacks update, so
  // it is re-evaluated after every render; it starts a run only when allowed.
  useEffect(() => {
    if (scoringState === "current") blockedFor.current = null
    if (sampleId == null || !settled || inFlight.current || scoringState === "current") return
    const key = `${sampleId}:${scoringState ?? "unknown"}`
    if (blockedFor.current === key || autoRuns.current >= MAX_AUTO_RUNS) return
    inFlight.current = true
    autoRuns.current += 1
    run(undefined, {
      onSuccess: () => {
        inFlight.current = false
        if (scoringState === undefined) blockedFor.current = key
      },
      onError: () => {
        inFlight.current = false
        blockedFor.current = key
      },
    })
  })
}
