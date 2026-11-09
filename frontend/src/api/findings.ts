/** React Query hooks for the unified findings API (P3-43). */

import { throwApiError } from "@/api/errors"

import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import type { Finding, FindingsSummaryResponse } from "@/types/findings"

/**
 * A growing, bounded window of findings, sorted by evidence (highest first).
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
  const limit = options?.limit ?? 200
  const offset = options?.offset

  return useInfiniteQuery({
    // Distinguish the infinite-query cache shape from older array-valued keys.
    // Context changes start a separate query; never borrow their old findings.
    queryKey: ["findings", sampleId, module, category, minStars, limit, offset, "window"],
    initialPageParam: limit,
    queryFn: async ({ pageParam }): Promise<Finding[]> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      if (module) params.set("module", module)
      if (category) params.set("category", category)
      if (minStars != null) params.set("min_stars", String(minStars))
      // Bound the fetch (#1303): a typical sample has tens of thousands of
      // findings (~132 MB) which froze/crashed the tab when fetched unbounded.
      params.set("limit", String(pageParam))
      if (offset != null && offset > 0) params.set("offset", String(offset))
      const res = await fetch(`/api/analysis/findings?${params}`)
      if (!res.ok) {
        await throwApiError(res, `Findings failed. Please try again.`)
      }
      return res.json()
    },
    // Each response replaces the cumulative window, rather than appending its
    // repeated prefix. Keep one window so refetches do not replay every bound.
    maxPages: 1,
    getNextPageParam: (lastPage, _pages, lastLimit) =>
      lastPage.length >= lastLimit ? lastLimit + limit : undefined,
    getPreviousPageParam: () => undefined,
    select: (data) => data.pages[0],
    enabled: sampleId != null,
    // Expose failures immediately, especially access/disclosure failures. The
    // page offers an explicit retry of the same bound for transient failures.
    retry: false,
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
        await throwApiError(res, `Findings summary failed. Please try again.`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}
