import { useEffect, useRef } from "react"
import type { ScoringState } from "@/types/route-scoring"

/**
 * Score a route-only module once its stored result is known to be missing or
 * stale, instead of on every visit (#2118). A run takes minutes on a full array.
 *
 * Waits for the stored result to load, then runs at most once per sample and
 * state for this mount, so a failed run does not loop and React StrictMode's
 * double effect does not start two runs. A response without a state (an older
 * backend) is scored, as every visit was before.
 */
export function useAutoScore(
  sampleId: number | null,
  loaded: boolean,
  scoringState: ScoringState | undefined,
  run: () => void,
): void {
  const requested = useRef<string | null>(null)
  useEffect(() => {
    if (sampleId == null || !loaded || scoringState === "current") return
    const key = `${sampleId}:${scoringState ?? "unknown"}`
    if (requested.current === key) return
    requested.current = key
    run()
  }, [sampleId, loaded, scoringState, run])
}
