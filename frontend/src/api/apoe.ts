/** React Query hooks for APOE module API (P3-22d). */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import type {
  APOEGateDisclaimerResponse,
  APOEGateStatusResponse,
  APOEGenotypeResponse,
  APOEFindingsListResponse,
} from "@/types/apoe"

/**
 * APOE gate disclosure text (hardcoded in disclaimers.py).
 * Not sample-specific — shared reference data.
 */
export function useAPOEDisclaimer() {
  return useQuery({
    queryKey: ["apoe-disclaimer"],
    queryFn: async (): Promise<APOEGateDisclaimerResponse> => {
      const res = await fetch("/api/analysis/apoe/disclaimer")
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`APOE disclaimer failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    staleTime: Infinity,
  })
}

/**
 * APOE gate acknowledgment state for a sample.
 * Checked before showing findings.
 */
export function useAPOEGateStatus(sampleId: number | null) {
  return useQuery({
    queryKey: ["apoe-gate-status", sampleId],
    queryFn: async (): Promise<APOEGateStatusResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/apoe/gate-status?${params}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`APOE gate status failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Acknowledge the APOE disclosure gate for a sample.
 * Invalidates gate status and findings queries on success.
 */
export function useAcknowledgeAPOEGate() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (sampleId: number) => {
      const params = new URLSearchParams({ sample_id: String(sampleId) })
      const res = await fetch(`/api/analysis/apoe/acknowledge-gate?${params}`, {
        method: "POST",
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`APOE gate acknowledge failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    onSuccess: (_data, sampleId) => {
      queryClient.invalidateQueries({ queryKey: ["apoe-gate-status", sampleId] })
      queryClient.invalidateQueries({ queryKey: ["apoe-findings", sampleId] })
      // The genotype query (staleTime: Infinity) cached the pre-ack
      // `determined_but_locked` response; refetch it so the card shows the
      // now-unlocked `determined` genotype instead of a blank card (#976).
      queryClient.invalidateQueries({ queryKey: ["apoe-genotype", sampleId] })
    },
  })
}

/**
 * Basic APOE genotype information (not gate-protected).
 * Shows diplotype and e4/e2 presence without clinical implications.
 */
export function useAPOEGenotype(sampleId: number | null) {
  return useQuery({
    queryKey: ["apoe-genotype", sampleId],
    queryFn: async (): Promise<APOEGenotypeResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/apoe/genotype?${params}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`APOE genotype failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * APOE findings (gate-protected).
 * Returns 403 if gate not acknowledged — only enabled when gate is acknowledged.
 */
export function useAPOEFindings(sampleId: number | null, gateAcknowledged: boolean) {
  return useQuery({
    queryKey: ["apoe-findings", sampleId],
    queryFn: async (): Promise<APOEFindingsListResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/apoe/findings?${params}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`APOE findings failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null && gateAcknowledged,
    staleTime: Infinity,
  })
}
