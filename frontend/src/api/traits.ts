/** React Query hooks for Traits & Personality API (P3-64). */

import { useQuery } from "@tanstack/react-query"
import type {
  PathwaysResponse,
  PathwayDetailResponse,
  PRSResponse,
  DisclaimerResponse,
} from "@/types/traits"

/**
 * All traits pathway results for a sample.
 * Returns pathway summaries with categorical levels, cross-module findings,
 * and the module disclaimer.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useTraitsPathways(sampleId: number | null) {
  return useQuery({
    queryKey: ["traits-pathways", sampleId],
    queryFn: async (): Promise<PathwaysResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/traits/pathways?${params}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Traits pathways failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Detailed results for a single traits pathway.
 * Returns per-SNP breakdown with genotypes, effect summaries,
 * trait domains, coverage notes, and cross-module links.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useTraitsPathwayDetail(
  pathwayId: string | null,
  sampleId: number | null,
) {
  return useQuery({
    queryKey: ["traits-pathway-detail", pathwayId, sampleId],
    queryFn: async (): Promise<PathwayDetailResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(
        `/api/analysis/traits/pathway/${encodeURIComponent(pathwayId!)}?${params}`,
      )
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(
          `Traits pathway detail failed: ${res.status}${text ? ` - ${text}` : ""}`,
        )
      }
      return res.json()
    },
    enabled: pathwayId != null && sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * PRS results for the traits module.
 * Returns educational attainment and cognitive ability PRS percentiles
 * with ancestry mismatch warnings and "Research Use Only" labels.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useTraitsPRS(sampleId: number | null) {
  return useQuery({
    queryKey: ["traits-prs", sampleId],
    queryFn: async (): Promise<PRSResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/traits/prs?${params}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Traits PRS failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Module-level disclaimer text.
 * Cached with staleTime: Infinity since the disclaimer is static.
 */
export function useTraitsDisclaimer() {
  return useQuery({
    queryKey: ["traits-disclaimer"],
    queryFn: async (): Promise<DisclaimerResponse> => {
      const res = await fetch("/api/analysis/traits/disclaimer")
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Traits disclaimer failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    staleTime: Infinity,
  })
}
