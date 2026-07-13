/** React Query hooks for ancestry module API (P3-27, P3-34, AMv2 Step 6). */

import { useRef } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type {
  AncestryFindingResponse,
  HaplogroupResponse,
  LAIProgressResponse,
  LAIResultResponse,
  LAIStatusResponse,
  LAITriggerResponse,
  PCACoordinatesResponse,
} from "@/types/ancestry"

/**
 * Ancestry inference findings for a sample (P3-23/P3-24).
 * Returns admixture fractions, top population, and coverage stats.
 * Cached with staleTime: Infinity since annotation data doesn't change.
 */
export function useAncestryFindings(sampleId: number | null) {
  return useQuery({
    queryKey: ["ancestry-findings", sampleId],
    queryFn: async (): Promise<AncestryFindingResponse | null> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/ancestry/findings?${params}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Ancestry findings failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * PCA coordinates for scatter plot visualization (P3-25).
 * Returns user coordinates + reference panel for Plotly scatter.
 * Cached with staleTime: Infinity since PCA data doesn't change.
 */
export function usePCACoordinates(sampleId: number | null) {
  return useQuery({
    queryKey: ["ancestry-pca-coordinates", sampleId],
    queryFn: async (): Promise<PCACoordinatesResponse | null> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/ancestry/pca-coordinates?${params}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`PCA coordinates failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * Haplogroup assignments for a sample (P3-34).
 * Returns mt and/or Y haplogroup assignments with traversal paths.
 * Cached with staleTime: Infinity since haplogroup data doesn't change.
 */
export function useHaplogroups(sampleId: number | null) {
  return useQuery({
    queryKey: ["ancestry-haplogroups", sampleId],
    queryFn: async (): Promise<HaplogroupResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId!) })
      const res = await fetch(`/api/analysis/ancestry/haplogroups?${params}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Haplogroup fetch failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * LAI bundle and Java availability status.
 * Used to show/hide chromosome painting section on AncestryView.
 * Cached with staleTime of 1 hour since availability changes infrequently.
 */
export function useLAIStatus() {
  return useQuery({
    queryKey: ["lai-status"],
    queryFn: async (): Promise<LAIStatusResponse> => {
      const res = await fetch("/api/analysis/ancestry/lai/status")
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`LAI status failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    staleTime: 3_600_000, // 1 hour
  })
}

/**
 * LAI analysis results for a sample.
 * Returns chromosome painting + global ancestry, or null if not run.
 * Cached with staleTime: Infinity since LAI results don't change.
 */
export function useLAIResults(sampleId: number | null) {
  return useQuery({
    queryKey: ["lai-results", sampleId],
    queryFn: async (): Promise<LAIResultResponse | null> => {
      const res = await fetch(`/api/analysis/ancestry/lai/${sampleId}/results`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`LAI results failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

/**
 * LAI analysis progress polling for a sample.
 * Polls every 3 seconds while enabled. Calls onComplete when job finishes.
 */
export function useLAIProgress(
  sampleId: number | null,
  enabled: boolean,
  onComplete?: () => void,
) {
  const completeFiredRef = useRef(false)

  return useQuery({
    queryKey: ["lai-progress", sampleId],
    queryFn: async (): Promise<LAIProgressResponse | null> => {
      const res = await fetch(`/api/analysis/ancestry/lai/${sampleId}/progress`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`LAI progress failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      const data: LAIProgressResponse | null = await res.json()
      if (data?.status === "complete" && onComplete && !completeFiredRef.current) {
        completeFiredRef.current = true
        onComplete()
      }
      // Reset guard when job is not terminal (new job started)
      if (data?.status === "running" || data?.status === "pending") {
        completeFiredRef.current = false
      }
      return data
    },
    enabled: enabled && sampleId != null,
    refetchInterval: ({ state: { data } }) =>
      data?.status === "complete" || data?.status === "failed" ? false : 3_000,
  })
}

/**
 * Mutation to trigger LAI analysis for a sample.
 * Returns a job_id for progress tracking.
 */
export function useTriggerLAI() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (sampleId: number): Promise<LAITriggerResponse> => {
      const res = await fetch(`/api/analysis/ancestry/lai/${sampleId}`, {
        method: "POST",
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`LAI trigger failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    onSuccess: (_data, sampleId) => {
      queryClient.invalidateQueries({ queryKey: ["lai-progress", sampleId] })
    },
  })
}
