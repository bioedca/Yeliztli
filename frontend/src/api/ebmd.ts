/** eBMD module API hooks (SW-B7). Route-only: POST /run then GET /prs. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { EbmdResponse } from "@/types/ebmd"

export function useEbmd(sampleId: number | null) {
  return useQuery({
    queryKey: ["ebmd", sampleId],
    queryFn: async (): Promise<EbmdResponse> => {
      const res = await fetch(`/api/analysis/ebmd/prs?sample_id=${sampleId}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`eBMD failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

export function useRunEbmd(sampleId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      if (sampleId == null) throw new Error("Cannot run eBMD analysis: sample ID is required")
      const res = await fetch(`/api/analysis/ebmd/run?sample_id=${sampleId}`, { method: "POST" })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`eBMD run failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ebmd", sampleId] }),
  })
}
