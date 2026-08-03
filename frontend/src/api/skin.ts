/** React Query hooks for Gene Skin API (P3-56). */

import { throwApiError } from "@/api/errors"

import { useQuery } from "@tanstack/react-query"
import type { PathwaysResponse, PathwayDetailResponse } from "@/types/skin"

/**
 * All skin pathway results for a sample.
 * Returns four pathway summaries (Pigmentation & UV Response,
 * Skin Barrier & Inflammation, Oxidative Stress & Aging,
 * Skin Micronutrients) with MC1R aggregate and cross-module findings.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useSkinPathways(sampleId: number | null) {
  return useQuery({
    queryKey: ["skin-pathways", sampleId],
    queryFn: async (): Promise<PathwaysResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/skin/pathways?${params}`)
      if (!res.ok) {
        await throwApiError(res, `Skin pathways failed. Please try again.`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Detailed results for a single skin pathway.
 * Returns per-SNP breakdown with genotypes, MC1R allele classes,
 * coverage notes, effect summaries, and recommendations.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useSkinPathwayDetail(
  pathwayId: string | null,
  sampleId: number | null,
) {
  return useQuery({
    queryKey: ["skin-pathway-detail", pathwayId, sampleId],
    queryFn: async (): Promise<PathwayDetailResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(
        `/api/analysis/skin/pathway/${encodeURIComponent(pathwayId!)}?${params}`,
      )
      if (!res.ok) {
        await throwApiError(res, `Skin pathway detail failed. Please try again.`)
      }
      return res.json()
    },
    enabled: pathwayId != null && sampleId != null,
    staleTime: Infinity,
  })
}
