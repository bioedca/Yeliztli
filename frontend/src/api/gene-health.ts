/** React Query hooks for Gene Health API (P3-66). */

import { throwApiError } from "@/api/errors"

import { useQuery } from "@tanstack/react-query"
import type { PathwaysResponse, PathwayDetailResponse } from "@/types/gene-health"

/**
 * All gene-health pathway results for a sample.
 * Returns four pathway summaries (Neurological, Metabolic, Autoimmune, Sensory)
 * with cross-module findings and module disclaimer.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useGeneHealthPathways(sampleId: number | null) {
  return useQuery({
    queryKey: ["gene-health-pathways", sampleId],
    queryFn: async (): Promise<PathwaysResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/gene_health/pathways?${params}`)
      if (!res.ok) {
        await throwApiError(res, `Gene Health pathways failed. Please try again.`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Detailed results for a single gene-health pathway.
 * Returns per-SNP breakdown with genotypes, effect summaries,
 * coverage notes, recommendations, and cross-module links.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useGeneHealthPathwayDetail(
  pathwayId: string | null,
  sampleId: number | null,
) {
  return useQuery({
    queryKey: ["gene-health-pathway-detail", pathwayId, sampleId],
    queryFn: async (): Promise<PathwayDetailResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(
        `/api/analysis/gene_health/pathway/${encodeURIComponent(pathwayId!)}?${params}`,
      )
      if (!res.ok) {
        await throwApiError(res, `Gene Health pathway detail failed. Please try again.`)
      }
      return res.json()
    },
    enabled: pathwayId != null && sampleId != null,
    staleTime: Infinity,
  })
}
