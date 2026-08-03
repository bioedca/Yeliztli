/** React Query hooks for nutrigenomics API (P3-11). */

import { throwApiError } from "@/api/errors"

import { useQuery } from "@tanstack/react-query"
import type { PathwaysResponse, PathwayDetailResponse } from "@/types/nutrigenomics"

/**
 * All nutrigenomics pathway results for a sample.
 * Returns six pathway summaries with categorical levels.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useNutrigenomicsPathways(sampleId: number | null) {
  return useQuery({
    queryKey: ["nutrigenomics-pathways", sampleId],
    queryFn: async (): Promise<PathwaysResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/nutrigenomics/pathways?${params}`)
      if (!res.ok) {
        await throwApiError(res, `Nutrigenomics pathways failed. Please try again.`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Detailed results for a single nutrient pathway.
 * Returns per-SNP breakdown with genotypes, effect summaries, and recommendations.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useNutrigenomicsPathwayDetail(
  pathwayId: string | null,
  sampleId: number | null,
) {
  return useQuery({
    queryKey: ["nutrigenomics-pathway-detail", pathwayId, sampleId],
    queryFn: async (): Promise<PathwayDetailResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(
        `/api/analysis/nutrigenomics/pathway/${encodeURIComponent(pathwayId!)}?${params}`,
      )
      if (!res.ok) {
        await throwApiError(res, `Nutrigenomics pathway detail failed. Please try again.`)
      }
      return res.json()
    },
    enabled: pathwayId != null && sampleId != null,
    staleTime: Infinity,
  })
}
