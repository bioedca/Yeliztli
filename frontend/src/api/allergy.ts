/** React Query hooks for Gene Allergy API (P3-61). */

import { throwApiError } from "@/api/errors"

import { useQuery } from "@tanstack/react-query"
import type { PathwaysResponse, PathwayDetailResponse } from "@/types/allergy"

/**
 * All allergy pathway results for a sample.
 * Returns four pathway summaries (Atopic Conditions, Drug Hypersensitivity,
 * Food Sensitivity, Histamine Metabolism) with celiac/histamine combined
 * assessments and cross-module findings.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useAllergyPathways(sampleId: number | null) {
  return useQuery({
    queryKey: ["allergy-pathways", sampleId],
    queryFn: async (): Promise<PathwaysResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/allergy/pathways?${params}`)
      if (!res.ok) {
        await throwApiError(res, `Allergy pathways failed. Please try again.`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Detailed results for a single allergy pathway.
 * Returns per-SNP breakdown with genotypes, HLA proxy data,
 * coverage notes, effect summaries, and recommendations.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useAllergyPathwayDetail(
  pathwayId: string | null,
  sampleId: number | null,
) {
  return useQuery({
    queryKey: ["allergy-pathway-detail", pathwayId, sampleId],
    queryFn: async (): Promise<PathwayDetailResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(
        `/api/analysis/allergy/pathway/${encodeURIComponent(pathwayId!)}?${params}`,
      )
      if (!res.ok) {
        await throwApiError(res, `Allergy pathway detail failed. Please try again.`)
      }
      return res.json()
    },
    enabled: pathwayId != null && sampleId != null,
    staleTime: Infinity,
  })
}
