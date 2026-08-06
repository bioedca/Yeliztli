/** React Query hooks for Gene Sleep API (P3-50). */

import { throwApiError } from "@/api/errors"

import { useQuery } from "@tanstack/react-query"
import type { PathwaysResponse, PathwayDetailResponse } from "@/types/sleep"

/**
 * All sleep pathway results for a sample.
 * Returns four pathway summaries (Caffeine & Sleep, Chronotype & Circadian
 * Rhythm, Sleep Quality, Sleep Disorders) with categorical levels,
 * cross-module findings, and CYP1A2 metabolizer state.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useSleepPathways(sampleId: number | null) {
  return useQuery({
    queryKey: ["sleep-pathways", sampleId],
    queryFn: async (): Promise<PathwaysResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/sleep/pathways?${params}`)
      if (!res.ok) {
        await throwApiError(res, `Sleep pathways failed. Please try again.`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Detailed results for a single sleep pathway.
 * Returns per-SNP breakdown with genotypes, CYP1A2 metabolizer states,
 * HLA/PER3 coverage notes, effect summaries, and recommendations.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useSleepPathwayDetail(
  pathwayId: string | null,
  sampleId: number | null,
) {
  return useQuery({
    queryKey: ["sleep-pathway-detail", pathwayId, sampleId],
    queryFn: async (): Promise<PathwayDetailResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(
        `/api/analysis/sleep/pathway/${encodeURIComponent(pathwayId!)}?${params}`,
      )
      if (!res.ok) {
        await throwApiError(res, `Sleep pathway detail failed. Please try again.`)
      }
      return res.json()
    },
    enabled: pathwayId != null && sampleId != null,
    staleTime: Infinity,
  })
}
