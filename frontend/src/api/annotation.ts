/** React Query hooks for annotation API (P2-06).
 *
 * - useStartAnnotation: POST /api/annotation/{sample_id} → 202 with job_id
 * - useCancelAnnotation: POST /api/annotation/cancel/{job_id}
 * - useAnnotationProgress: SSE-based hook for real-time progress tracking
 */

import { useState, useEffect, useRef, useCallback } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { qcMetricsQueryKey } from "@/api/qc"

// ── Types ──────────────────────────────────────────────────────────────

export interface AnnotationJobResult {
  job_id: string
  sample_id: number
  status: "pending"
}

export interface AnnotationProgress {
  job_id: string
  status: "pending" | "running" | "complete" | "failed" | "cancelled"
  progress_pct: number
  message: string
  error: string | null
}

// ── Start annotation mutation ──────────────────────────────────────────

export function useStartAnnotation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (sampleId: number): Promise<AnnotationJobResult> => {
      const res = await fetch(`/api/annotation/${sampleId}`, {
        method: "POST",
      })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        const detail = body?.detail || `Start annotation failed: ${res.status}`
        throw new Error(detail)
      }
      return await res.json()
    },
    onSuccess: (_result, sampleId) => {
      queryClient.invalidateQueries({ queryKey: ["variants-count"] })
      queryClient.invalidateQueries({ queryKey: qcMetricsQueryKey(sampleId) })
    },
  })
}

// ── Cancel annotation mutation ─────────────────────────────────────────

export function useCancelAnnotation() {
  return useMutation({
    mutationFn: async (jobId: string): Promise<{ job_id: string; status: string }> => {
      const res = await fetch(`/api/annotation/cancel/${jobId}`, {
        method: "POST",
      })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        const detail = body?.detail || `Cancel failed: ${res.status}`
        throw new Error(detail)
      }
      return await res.json()
    },
  })
}

// ── Active job query ──────────────────────────────────────────────────

export interface ActiveAnnotationJob {
  job_id: string
  sample_id: number
  status: "pending" | "running"
  progress_pct: number
  message: string
}

/** Check if a sample has an active (pending/running) annotation job. */
export function useActiveAnnotationJob(sampleId: number | null) {
  return useQuery<ActiveAnnotationJob | null>({
    queryKey: ["annotation-active", sampleId],
    queryFn: async () => {
      if (sampleId == null) return null
      const res = await fetch(`/api/annotation/active/${sampleId}`)
      if (res.status === 404) return null
      if (!res.ok) throw new Error(`Failed to check active job: ${res.status}`)
      return await res.json()
    },
    enabled: sampleId != null,
    staleTime: 0,
    refetchOnWindowFocus: false,
  })
}

// ── SSE progress hook ──────────────────────────────────────────────────

const TERMINAL_STATES = new Set(["complete", "failed", "cancelled"])

export function useAnnotationProgress(
  jobId: string | null,
  sampleId: number | null = null,
): AnnotationProgress | null {
  const [progress, setProgress] = useState<AnnotationProgress | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)
  const queryClient = useQueryClient()
  const cleanup = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
      eventSourceRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!jobId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setProgress(null)
      return
    }

    cleanup()

    const es = new EventSource(`/api/annotation/status/${jobId}`)
    eventSourceRef.current = es

    es.addEventListener("progress", (event: MessageEvent) => {
      let data: AnnotationProgress
      try {
        data = JSON.parse(event.data)
      } catch {
        return
      }
      setProgress(data)

      if (TERMINAL_STATES.has(data.status)) {
        es.close()
        eventSourceRef.current = null
        // Invalidate variant queries so tables refresh with new annotations
        queryClient.invalidateQueries({ queryKey: ["variants"] })
        queryClient.invalidateQueries({ queryKey: ["variants-count"] })
        queryClient.invalidateQueries({ queryKey: ["variants-total-count"] })
        queryClient.invalidateQueries({ queryKey: ["variants-qc-stats"] })
        if (sampleId != null) {
          queryClient.invalidateQueries({ queryKey: qcMetricsQueryKey(sampleId) })
        }
        queryClient.invalidateQueries({ queryKey: ["variants-chromosomes"] })
        // Invalidate findings so High-Confidence Findings refreshes
        queryClient.invalidateQueries({ queryKey: ["findings-summary"] })
        queryClient.invalidateQueries({ queryKey: ["findings"] })
      }
    })

    es.addEventListener("error", () => {
      es.close()
      eventSourceRef.current = null
    })

    return cleanup
  }, [jobId, cleanup, queryClient, sampleId])

  return progress
}
