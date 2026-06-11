/** Metabolic module API hooks (SW-B5). Route-only module: a POST /run computes
 * the scores, then the GET endpoints read them back. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type {
  MetabolicAnchorListResponse,
  MetabolicPRSListResponse,
} from "@/types/metabolic"

async function getJson<T>(url: string, label: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) {
    const text = await res.text().catch(() => "")
    throw new Error(`${label} failed: ${res.status}${text ? ` - ${text}` : ""}`)
  }
  return res.json()
}

export function useMetabolicPRS(sampleId: number | null) {
  return useQuery({
    queryKey: ["metabolic-prs", sampleId],
    queryFn: () =>
      getJson<MetabolicPRSListResponse>(
        `/api/analysis/metabolic/prs?sample_id=${sampleId}`,
        "Metabolic PRS",
      ),
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

export function useMetabolicAnchors(sampleId: number | null) {
  return useQuery({
    queryKey: ["metabolic-anchors", sampleId],
    queryFn: () =>
      getJson<MetabolicAnchorListResponse>(
        `/api/analysis/metabolic/anchors?sample_id=${sampleId}`,
        "Metabolic anchors",
      ),
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/** Trigger the route-only computation, then refetch the GET queries. */
export function useRunMetabolic(sampleId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      if (sampleId == null) throw new Error("Cannot run metabolic analysis: sample ID is required")
      const res = await fetch(`/api/analysis/metabolic/run?sample_id=${sampleId}`, {
        method: "POST",
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Metabolic run failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["metabolic-prs", sampleId] })
      qc.invalidateQueries({ queryKey: ["metabolic-anchors", sampleId] })
    },
  })
}
