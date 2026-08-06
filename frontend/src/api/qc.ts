import { throwApiError } from "@/api/errors"

import { useQuery } from '@tanstack/react-query'
import type { QCMetrics } from '@/types/qc'

const qcMetricsQueryKey = (sampleId: number | null) =>
  ['analysis-qc-metrics', sampleId] as const

export function useQCMetrics(sampleId: number | null) {
  return useQuery({
    queryKey: qcMetricsQueryKey(sampleId),
    queryFn: async (): Promise<QCMetrics> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/qc/metrics?${params}`)
      if (!res.ok) {
        await throwApiError(res, `QC metrics failed. Please try again.`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}
