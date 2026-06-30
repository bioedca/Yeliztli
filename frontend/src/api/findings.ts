/** React Query hooks for the unified findings API (P3-43). */

import { useQuery } from "@tanstack/react-query"
import type { Finding, FindingsSummaryResponse } from "@/types/findings"

/**
 * All findings for a sample, sorted by evidence level (highest first).
 * Supports optional filtering by module, category, and minimum stars.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useFindings(
  sampleId: number | null,
  options?: {
    module?: string
    category?: string
    minStars?: number
    limit?: number
    offset?: number
  },
) {
  const module = options?.module
  const category = options?.category
  const minStars = options?.minStars
  const limit = options?.limit
  const offset = options?.offset

  return useQuery({
    queryKey: ["findings", sampleId, module, category, minStars, limit, offset],
    queryFn: async (): Promise<Finding[]> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      if (module) params.set("module", module)
      if (category) params.set("category", category)
      if (minStars != null) params.set("min_stars", String(minStars))
      // Bound the fetch (#1303): a typical sample has tens of thousands of
      // findings (~132 MB) which froze/crashed the tab when fetched unbounded.
      if (limit != null) params.set("limit", String(limit))
      if (offset != null && offset > 0) params.set("offset", String(offset))
      const res = await fetch(`/api/analysis/findings?${params}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Findings failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Per-module summary with counts and top findings.
 * Cached with staleTime: Infinity since findings don't change until re-annotation.
 */
export function useFindingsSummary(sampleId: number | null) {
  return useQuery({
    queryKey: ["findings-summary", sampleId],
    queryFn: async (): Promise<FindingsSummaryResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/findings/summary?${params}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Findings summary failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}
