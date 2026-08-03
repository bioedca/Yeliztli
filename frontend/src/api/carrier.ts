/** React Query hooks for carrier status module API (P3-38). */

import { throwApiError } from "@/api/errors"

import { useQuery } from "@tanstack/react-query"
import type {
  CarrierVariantsListResponse,
  CarrierDisclaimerResponse,
} from "@/types/carrier"

/**
 * Carrier-module P/LP findings for a sample.
 * Includes heterozygous carrier findings and AR affected-status patterns.
 * Cached with staleTime: Infinity since annotation data doesn't change.
 */
export function useCarrierVariants(sampleId: number | null) {
  return useQuery({
    queryKey: ["carrier-variants", sampleId],
    queryFn: async (): Promise<CarrierVariantsListResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/carrier/variants?${params}`)
      if (!res.ok) {
        await throwApiError(res, `Carrier variants failed. Please try again.`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Carrier status disclaimer text with per-gene notes (P3-37).
 * Not sample-specific — shared reference data.
 * Cached with staleTime: Infinity since disclaimer text doesn't change.
 */
export function useCarrierDisclaimer() {
  return useQuery({
    queryKey: ["carrier-disclaimer"],
    queryFn: async (): Promise<CarrierDisclaimerResponse> => {
      const res = await fetch("/api/analysis/carrier/disclaimer")
      if (!res.ok) {
        await throwApiError(res, `Carrier disclaimer failed. Please try again.`)
      }
      return res.json()
    },
    staleTime: Infinity,
  })
}
