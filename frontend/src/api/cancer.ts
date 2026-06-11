/** React Query hooks for cancer module API (P3-18). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type {
  AbsoluteRiskResponse,
  CancerVariantsListResponse,
  CancerPRSListResponse,
  CancerDisclaimerResponse,
} from "@/types/cancer"

/**
 * Cancer P/LP variant findings for a sample.
 * Monogenic pathogenic variants from the 28-gene cancer panel.
 * Cached with staleTime: Infinity since annotation data doesn't change.
 */
export function useCancerVariants(sampleId: number | null) {
  return useQuery({
    queryKey: ["cancer-variants", sampleId],
    queryFn: async (): Promise<CancerVariantsListResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/cancer/variants?${params}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Cancer variants failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Cancer PRS results (breast, prostate, colorectal, melanoma).
 * Secondary "Research Use Only" tier with bootstrap CI gauges.
 * Cached with staleTime: Infinity since PRS data doesn't change until re-annotation.
 */
export function useCancerPRS(sampleId: number | null) {
  return useQuery({
    queryKey: ["cancer-prs", sampleId],
    queryFn: async (): Promise<CancerPRSListResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/cancer/prs?${params}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Cancer PRS failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Cancer module disclaimer text (P3-17).
 * Not sample-specific — shared reference data.
 * Cached with staleTime: Infinity since disclaimer text doesn't change.
 */
export function useCancerDisclaimer() {
  return useQuery({
    queryKey: ["cancer-disclaimer"],
    queryFn: async (): Promise<CancerDisclaimerResponse> => {
      const res = await fetch("/api/analysis/cancer/disclaimer")
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Cancer disclaimer failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    staleTime: Infinity,
  })
}

/** Breast absolute-risk overlay (SW-B8, opt-in). */
export function useAbsoluteRisk(sampleId: number | null) {
  return useQuery({
    queryKey: ["cancer-absolute-risk", sampleId],
    queryFn: async (): Promise<AbsoluteRiskResponse> => {
      const res = await fetch(`/api/analysis/cancer/absolute-risk?sample_id=${sampleId}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Absolute risk failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/** Set the breast absolute-risk opt-in consent, then refetch the overlay. */
export function useSetAbsoluteRiskConsent(sampleId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (consented: boolean) => {
      const res = await fetch(
        `/api/analysis/cancer/absolute-risk/consent?sample_id=${sampleId}&consented=${consented}`,
        { method: "POST" },
      )
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Consent failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cancer-absolute-risk", sampleId] }),
  })
}
