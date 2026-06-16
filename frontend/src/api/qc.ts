import { useQuery } from '@tanstack/react-query'
import type { QCMetrics } from '@/types/qc'

export const qcMetricsQueryKey = (sampleId: number | null) =>
  ['analysis-qc-metrics', sampleId] as const

export function useQCMetrics(sampleId: number | null) {
  return useQuery({
    queryKey: qcMetricsQueryKey(sampleId),
    queryFn: async (): Promise<QCMetrics> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/qc/metrics?${params}`)
      if (!res.ok) {
        const text = await res.text().catch(() => '')
        throw new Error(`QC metrics failed: ${res.status}${text ? ` - ${text}` : ''}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}
