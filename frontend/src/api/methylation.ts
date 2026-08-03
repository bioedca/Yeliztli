/** React Query hooks for MTHFR & Methylation API (P3-53). */

import { throwApiError } from "@/api/errors"

import { useQuery } from "@tanstack/react-query"
import type { PathwaysResponse, PathwayDetailResponse } from "@/types/methylation"

/**
 * All methylation pathway results for a sample.
 * Returns five pathway summaries with categorical levels and compound het info.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useMethylationPathways(sampleId: number | null) {
  return useQuery({
    queryKey: ["methylation-pathways", sampleId],
    queryFn: async (): Promise<PathwaysResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/methylation/pathways?${params}`)
      if (!res.ok) {
        await throwApiError(res, `Methylation pathways failed. Please try again.`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Detailed results for a single methylation pathway.
 * Returns per-SNP breakdown with genotypes, effect summaries, coverage notes.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useMethylationPathwayDetail(
  pathwayId: string | null,
  sampleId: number | null,
) {
  return useQuery({
    queryKey: ["methylation-pathway-detail", pathwayId, sampleId],
    queryFn: async (): Promise<PathwayDetailResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(
        `/api/analysis/methylation/pathway/${encodeURIComponent(pathwayId!)}?${params}`,
      )
      if (!res.ok) {
        await throwApiError(res, `Methylation pathway detail failed. Please try again.`)
      }
      return res.json()
    },
    enabled: pathwayId != null && sampleId != null,
    staleTime: Infinity,
  })
}
