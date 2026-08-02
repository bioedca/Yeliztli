/** React Query hooks for annotation API (P2-06).
 *
 * - useStartAnnotation: POST /api/annotation/{sample_id} → 202 with job_id
 * - useCancelAnnotation: POST /api/annotation/cancel/{job_id}
 * - useAnnotationProgress: SSE-based hook for real-time progress tracking
 */

import { useState, useEffect, useRef, useCallback } from "react"
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query"
import { throwApiError } from "@/api/errors"
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
        await throwApiError(res, "Unable to start annotation. Please try again.")
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
        await throwApiError(res, "Unable to cancel annotation. Please try again.")
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

export const annotationActiveQueryKey = (sampleId: number | null) =>
  ["annotation-active", sampleId] as const

const ACTIVE_ANNOTATION_PROBE_TIMEOUT_MS = 10_000

export async function fetchActiveAnnotationJob(
  sampleId: number,
  querySignal: AbortSignal,
): Promise<ActiveAnnotationJob | null> {
  const requestController = new AbortController()
  const timeoutId = setTimeout(
    () => requestController.abort(),
    ACTIVE_ANNOTATION_PROBE_TIMEOUT_MS,
  )
  const abortFromQuery = () => requestController.abort()
  querySignal.addEventListener("abort", abortFromQuery, { once: true })

  try {
    const res = await fetch(`/api/annotation/active/${sampleId}`, {
      signal: requestController.signal,
    })
    if (res.status === 404 || !res.ok) return null
    return await res.json()
  } catch (error) {
    // Preserve TanStack Query's cancellation semantics, but make a timed-out
    // or unavailable auxiliary probe recoverable so stale recovery is not
    // permanently blanked behind a pending request.
    if (querySignal.aborted) throw error
    return null
  } finally {
    clearTimeout(timeoutId)
    querySignal.removeEventListener("abort", abortFromQuery)
  }
}

/** Check if a sample has an active (pending/running) annotation job. */
export function useActiveAnnotationJob(sampleId: number | null) {
  return useQuery<ActiveAnnotationJob | null>({
    queryKey: annotationActiveQueryKey(sampleId),
    queryFn: async ({ signal }) => {
      if (sampleId == null) return null
      return fetchActiveAnnotationJob(sampleId, signal)
    },
    enabled: sampleId != null,
    staleTime: 0,
    refetchOnWindowFocus: false,
    refetchInterval: ({ state: { data } }) => (data ? 1_000 : false),
  })
}

/** Invalidate data derived from an annotation bundle before stale views remount. */
export function invalidateAnnotationResultQueries(
  queryClient: QueryClient,
  sampleId: number | null = null,
) {
  const invalidations = [
    queryClient.invalidateQueries({ queryKey: ["variants"] }),
    queryClient.invalidateQueries({ queryKey: ["variants-count"] }),
    queryClient.invalidateQueries({ queryKey: ["variants-total-count"] }),
    queryClient.invalidateQueries({ queryKey: ["variants-qc-stats"] }),
    queryClient.invalidateQueries({ queryKey: ["variants-chromosomes"] }),
    queryClient.invalidateQueries({ queryKey: ["findings-summary"] }),
    queryClient.invalidateQueries({ queryKey: ["findings"] }),
  ]
  if (sampleId != null) {
    invalidations.push(
      queryClient.invalidateQueries({ queryKey: qcMetricsQueryKey(sampleId) }),
    )
  }
  return Promise.all(invalidations)
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
        void invalidateAnnotationResultQueries(queryClient, sampleId)
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
