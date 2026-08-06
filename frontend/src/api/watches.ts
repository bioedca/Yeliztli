/** React Query hooks for VUS watch/unwatch API (P4-21j). */

import { throwApiError } from "@/api/errors"

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"

export interface WatchedVariant {
  rsid: string
  watched_at: string
  clinvar_significance_at_watch: string | null
  clinvar_significance_current: string | null
  notes: string | null
}

/** Fetch all watched variants for a sample. */
export function useWatchedVariants(sampleId: number | null) {
  return useQuery({
    queryKey: ["watched-variants", sampleId],
    queryFn: async (): Promise<WatchedVariant[]> => {
      const res = await fetch(`/api/watches?sample_id=${sampleId}`)
      if (!res.ok) throw new Error("Failed to fetch watched variants")
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: 0,
  })
}

/** Watch a variant. */
export function useWatchVariant(sampleId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (rsid: string) => {
      if (sampleId == null) {
        throw new Error("Cannot watch variant without a sample")
      }
      const res = await fetch("/api/watches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sample_id: sampleId, rsid }),
      })
      if (!res.ok) {
        await throwApiError(res, "Failed to watch variant")
      }
      return res.json() as Promise<WatchedVariant>
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["watched-variants", sampleId] })
    },
  })
}

/** Unwatch a variant. */
export function useUnwatchVariant(sampleId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (rsid: string) => {
      if (sampleId == null) {
        throw new Error("Cannot unwatch variant without a sample")
      }
      const res = await fetch(
        `/api/watches/${encodeURIComponent(rsid)}?sample_id=${sampleId}`,
        { method: "DELETE" },
      )
      if (!res.ok) {
        await throwApiError(res, "Failed to unwatch variant")
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["watched-variants", sampleId] })
    },
  })
}
