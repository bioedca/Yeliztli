/** eBMD module API hooks (SW-B7). Route-only: POST /run then GET /prs. */

import { throwApiError } from "@/api/errors"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { EbmdResponse } from "@/types/ebmd"

export function useEbmd(sampleId: number | null) {
  return useQuery({
    queryKey: ["ebmd", sampleId],
    queryFn: async (): Promise<EbmdResponse> => {
      const res = await fetch(`/api/analysis/ebmd/prs?sample_id=${sampleId}`)
      if (!res.ok) {
        await throwApiError(res, `eBMD failed. Please try again.`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
    // The response carries scoring_state, which changes when the score's inputs
    // do (a database update, re-annotation) without this cache knowing (#2118).
    refetchOnMount: "always",
    // Re-checked on returning to the window too, as another tab may re-score.
    refetchOnWindowFocus: "always",
  })
}

export function useRunEbmd(sampleId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      if (sampleId == null) throw new Error("Cannot run eBMD analysis: sample ID is required")
      const res = await fetch(`/api/analysis/ebmd/run?sample_id=${sampleId}`, { method: "POST" })
      if (!res.ok) {
        await throwApiError(res, `eBMD run failed. Please try again.`)
      }
      return res.json()
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ebmd", sampleId] }),
  })
}
