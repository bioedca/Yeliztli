/** React Query hooks for cardiovascular module API (P3-21). */

import { throwApiError } from "@/api/errors"

import { useQuery } from "@tanstack/react-query"
import type {
  CardiovascularVariantsListResponse,
  FHStatusResponse,
  CardiovascularDisclaimerResponse,
} from "@/types/cardiovascular"

/**
 * Cardiovascular P/LP variant findings for a sample.
 * Monogenic pathogenic variants from the 16-gene cardiovascular panel.
 * Cached with staleTime: Infinity since annotation data doesn't change.
 */
export function useCardiovascularVariants(sampleId: number | null) {
  return useQuery({
    queryKey: ["cardiovascular-variants", sampleId],
    queryFn: async (): Promise<CardiovascularVariantsListResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/cardiovascular/variants?${params}`)
      if (!res.ok) {
        await throwApiError(res, `Cardiovascular variants failed. Please try again.`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * FH status determination for a sample (P3-20).
 * Returns Positive/Negative status with affected genes and variant details.
 * Cached with staleTime: Infinity since FH status doesn't change until re-annotation.
 */
export function useFHStatus(sampleId: number | null) {
  return useQuery({
    queryKey: ["cardiovascular-fh-status", sampleId],
    queryFn: async (): Promise<FHStatusResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/cardiovascular/fh-status?${params}`)
      if (!res.ok) {
        await throwApiError(res, `FH status failed. Please try again.`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Cardiovascular module disclaimer text.
 * Not sample-specific — shared reference data.
 * Cached with staleTime: Infinity since disclaimer text doesn't change.
 */
export function useCardiovascularDisclaimer() {
  return useQuery({
    queryKey: ["cardiovascular-disclaimer"],
    queryFn: async (): Promise<CardiovascularDisclaimerResponse> => {
      const res = await fetch("/api/analysis/cardiovascular/disclaimer")
      if (!res.ok) {
        await throwApiError(res, `Cardiovascular disclaimer failed. Please try again.`)
      }
      return res.json()
    },
    staleTime: Infinity,
  })
}
