/** React Query hooks for Gene Fitness API (P3-47). */

import { throwApiError } from "@/api/errors"

import { useQuery } from "@tanstack/react-query"
import type { PathwaysResponse, PathwayDetailResponse } from "@/types/fitness"

/**
 * All fitness pathway results for a sample.
 * Returns four pathway summaries (Endurance, Power, Recovery & Injury,
 * Training Response) with categorical levels and cross-context findings.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useFitnessPathways(sampleId: number | null) {
  return useQuery({
    queryKey: ["fitness-pathways", sampleId],
    queryFn: async (): Promise<PathwaysResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/fitness/pathways?${params}`)
      if (!res.ok) {
        await throwApiError(res, `Fitness pathways failed. Please try again.`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Detailed results for a single fitness pathway.
 * Returns per-SNP breakdown with genotypes, ACTN3 three-state labels,
 * ACE coverage notes, effect summaries, and recommendations.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useFitnessPathwayDetail(
  pathwayId: string | null,
  sampleId: number | null,
) {
  return useQuery({
    queryKey: ["fitness-pathway-detail", pathwayId, sampleId],
    queryFn: async (): Promise<PathwayDetailResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(
        `/api/analysis/fitness/pathway/${encodeURIComponent(pathwayId!)}?${params}`,
      )
      if (!res.ok) {
        await throwApiError(res, `Fitness pathway detail failed. Please try again.`)
      }
      return res.json()
    },
    enabled: pathwayId != null && sampleId != null,
    staleTime: Infinity,
  })
}
