/** FH module API hooks (SW-B6). Route-only: POST /run then GET /assessment. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { FhAssessment } from "@/types/fh"

export function useFhAssessment(sampleId: number | null) {
  return useQuery({
    queryKey: ["fh-assessment", sampleId],
    queryFn: async (): Promise<FhAssessment> => {
      const res = await fetch(`/api/analysis/fh/assessment?sample_id=${sampleId}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`FH assessment failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

export function useRunFh(sampleId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      if (sampleId == null) throw new Error("Cannot run FH analysis: sample ID is required")
      const res = await fetch(`/api/analysis/fh/run?sample_id=${sampleId}`, { method: "POST" })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`FH run failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["fh-assessment", sampleId] }),
  })
}
