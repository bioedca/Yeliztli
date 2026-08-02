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
import { ApiError, throwApiError } from "@/api/errors"

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
    onSuccess: (result, sampleId) => {
      // Starting a new annotation makes every cached result for this sample
      // untrustworthy. Do this at launch rather than relying solely on an SSE
      // observer: callers such as the individual page can start a job and
      // navigate away before any progress listener reaches its terminal event.
      // The running-job cache and staleness probe also notify an already-open
      // route gate that it must re-check the server before trusting its data.
      // Do not return any of these promises: an invalidated active result can
      // wait on the backend, but the accepted job must immediately leave the
      // mutation usable for callers that offer progress navigation.
      const activeResult: ActiveAnnotationJob = {
        ...result,
        sample_id: sampleId,
        progress_pct: 0,
        message: "Queued for annotation",
      }
      queryClient.setQueryData(annotationActiveQueryKey(sampleId), activeResult)
      void queryClient.invalidateQueries({
        queryKey: sampleStalenessQueryKeyPrefix(sampleId),
      })
      void invalidateAnnotationResultQueries(queryClient, sampleId)
    },
    onError: (error, sampleId) => {
      // A 409 means another request already started annotation. It carries the
      // same cache-safety consequence as this request's accepted 202, and is
      // especially important after a reload where no local progress observer
      // is available to invalidate on the terminal event.
      if (error instanceof ApiError && error.status === 409) {
        void queryClient.invalidateQueries({
          queryKey: annotationActiveQueryKey(sampleId),
        })
        void queryClient.invalidateQueries({
          queryKey: sampleStalenessQueryKeyPrefix(sampleId),
        })
        void invalidateAnnotationResultQueries(queryClient, sampleId)
      }
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

/** Shared prefix for the route-scoped freshness probes of one sample. */
export const sampleStalenessQueryKeyPrefix = (sampleId: number | null) =>
  ["sample-staleness", sampleId] as const

// Query-key contract for data scoped to one sample. Some keys place sampleId
// after a detail selector, so matching every numeric key segment would corrupt
// cache isolation when a pathway, limit, or overlay ID happens to equal a
// different sample ID. Add new sample-derived query keys here alongside their
// hook rather than relying on their positional coincidence.
const SAMPLE_DERIVED_QUERY_KEY_SHAPES: ReadonlyArray<{
  prefix: readonly unknown[]
  sampleIdIndex: number
}> = [
  { prefix: ["samples"], sampleIdIndex: 1 },
  { prefix: ["analysis-qc-metrics"], sampleIdIndex: 1 },
  { prefix: ["variants"], sampleIdIndex: 1 },
  { prefix: ["variants-count"], sampleIdIndex: 1 },
  { prefix: ["variants-chromosomes"], sampleIdIndex: 1 },
  { prefix: ["variants-qc-stats"], sampleIdIndex: 1 },
  { prefix: ["variants-search"], sampleIdIndex: 1 },
  { prefix: ["variants-total-count"], sampleIdIndex: 1 },
  { prefix: ["variants-unannotated-count"], sampleIdIndex: 1 },
  { prefix: ["findings"], sampleIdIndex: 1 },
  { prefix: ["findings-summary"], sampleIdIndex: 1 },
  { prefix: ["rare-variant-findings"], sampleIdIndex: 1 },
  { prefix: ["variant-detail"], sampleIdIndex: 2 },
  { prefix: ["gene-detail"], sampleIdIndex: 2 },
  { prefix: ["gene-health-pathways"], sampleIdIndex: 1 },
  { prefix: ["gene-health-pathway-detail"], sampleIdIndex: 2 },
  { prefix: ["fitness-pathways"], sampleIdIndex: 1 },
  { prefix: ["fitness-pathway-detail"], sampleIdIndex: 2 },
  { prefix: ["traits-pathways"], sampleIdIndex: 1 },
  { prefix: ["traits-pathway-detail"], sampleIdIndex: 2 },
  { prefix: ["traits-prs"], sampleIdIndex: 1 },
  { prefix: ["sleep-pathways"], sampleIdIndex: 1 },
  { prefix: ["sleep-pathway-detail"], sampleIdIndex: 2 },
  { prefix: ["methylation-pathways"], sampleIdIndex: 1 },
  { prefix: ["methylation-pathway-detail"], sampleIdIndex: 2 },
  { prefix: ["allergy-pathways"], sampleIdIndex: 1 },
  { prefix: ["allergy-pathway-detail"], sampleIdIndex: 2 },
  { prefix: ["skin-pathways"], sampleIdIndex: 1 },
  { prefix: ["skin-pathway-detail"], sampleIdIndex: 2 },
  { prefix: ["nutrigenomics-pathways"], sampleIdIndex: 1 },
  { prefix: ["nutrigenomics-pathway-detail"], sampleIdIndex: 2 },
  { prefix: ["hla-drug-hypersensitivity"], sampleIdIndex: 1 },
  { prefix: ["hla-rule-outs"], sampleIdIndex: 1 },
  { prefix: ["hla-susceptibility"], sampleIdIndex: 1 },
  { prefix: ["hla-alleles"], sampleIdIndex: 1 },
  { prefix: ["ancestry-findings"], sampleIdIndex: 1 },
  { prefix: ["ancestry-pca-coordinates"], sampleIdIndex: 1 },
  { prefix: ["ancestry-haplogroups"], sampleIdIndex: 1 },
  { prefix: ["lai-results"], sampleIdIndex: 1 },
  { prefix: ["lai-progress"], sampleIdIndex: 1 },
  { prefix: ["carrier-variants"], sampleIdIndex: 1 },
  { prefix: ["apoe-gate-status"], sampleIdIndex: 1 },
  { prefix: ["apoe-genotype"], sampleIdIndex: 1 },
  { prefix: ["apoe-findings"], sampleIdIndex: 1 },
  { prefix: ["cardiovascular-variants"], sampleIdIndex: 1 },
  { prefix: ["cardiovascular-fh-status"], sampleIdIndex: 1 },
  { prefix: ["cancer-variants"], sampleIdIndex: 1 },
  { prefix: ["cancer-prs"], sampleIdIndex: 1 },
  { prefix: ["cancer-absolute-risk"], sampleIdIndex: 1 },
  { prefix: ["metabolic-prs"], sampleIdIndex: 1 },
  { prefix: ["metabolic-anchors"], sampleIdIndex: 1 },
  { prefix: ["ebmd"], sampleIdIndex: 1 },
  { prefix: ["fh-assessment"], sampleIdIndex: 1 },
  { prefix: ["pgx-guidelines"], sampleIdIndex: 1 },
  { prefix: ["pharma-genes"], sampleIdIndex: 1 },
  { prefix: ["pharma-drug"], sampleIdIndex: 2 },
  { prefix: ["pharma-report"], sampleIdIndex: 1 },
  { prefix: ["watched-variants"], sampleIdIndex: 1 },
  { prefix: ["tags"], sampleIdIndex: 1 },
  { prefix: ["sql-schema"], sampleIdIndex: 1 },
  { prefix: ["overlay-results"], sampleIdIndex: 2 },
  { prefix: ["updates", "prompts"], sampleIdIndex: 2 },
  { prefix: ["updates", "finding-changes"], sampleIdIndex: 2 },
]

function isSampleDerivedQueryKey(queryKey: readonly unknown[], sampleId: number): boolean {
  return SAMPLE_DERIVED_QUERY_KEY_SHAPES.some(
    ({ prefix, sampleIdIndex }) =>
      queryKey[sampleIdIndex] === sampleId &&
      prefix.every((segment, index) => queryKey[index] === segment),
  )
}

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
    // A 404 is the only authoritative absence signal. Treat transport and
    // non-404 HTTP failures as observable query errors so callers recovering
    // from a 409 conflict can keep polling instead of mistaking an unavailable
    // probe for a completed annotation.
    if (res.status === 404) return null
    if (!res.ok) {
      await throwApiError(res, "Unable to check re-annotation status. Please try again.")
    }
    return await res.json()
  } finally {
    clearTimeout(timeoutId)
    querySignal.removeEventListener("abort", abortFromQuery)
  }
}

interface ActiveAnnotationJobQueryOptions {
  /** Continue one-second probes after a 409 has confirmed an active job. */
  pollWhenUnavailable?: boolean
  /** Suppress initial-error retries for auxiliary callers such as the stale gate. */
  retryOnInitialFailure?: boolean
}

/** Check if a sample has an active (pending/running) annotation job. */
export function useActiveAnnotationJob(
  sampleId: number | null,
  {
    pollWhenUnavailable = false,
    retryOnInitialFailure = true,
  }: ActiveAnnotationJobQueryOptions = {},
) {
  return useQuery<ActiveAnnotationJob | null>({
    queryKey: annotationActiveQueryKey(sampleId),
    queryFn: async ({ signal }) => {
      if (sampleId == null) return null
      return fetchActiveAnnotationJob(sampleId, signal)
    },
    enabled: sampleId != null,
    staleTime: 0,
    // Ordinary consumers retain their QueryClient retry policy, so a transient
    // active-job probe cannot expose a duplicate-start CTA. The stale gate
    // explicitly opts out on its initial auxiliary probe, then polls after a
    // 409 has authoritatively established that a job is active.
    ...(retryOnInitialFailure ? {} : { retry: false }),
    refetchOnWindowFocus: false,
    refetchInterval: ({ state: { data } }) => (data || pollWhenUnavailable ? 1_000 : false),
  })
}

/** Invalidate data derived from an annotation bundle before stale views remount. */
export function invalidateAnnotationResultQueries(
  queryClient: QueryClient,
  sampleId: number | null = null,
) {
  if (sampleId != null) {
    // Annotation-result keys do not share one prefix and the sample identifier
    // is not always in the same position (for example, `pharma-drug` includes
    // a drug name before it). Match the explicit sample-key contract so a
    // coincident pathway or pagination value cannot invalidate another sample.
    return queryClient.invalidateQueries({
      predicate: (query) => isSampleDerivedQueryKey(query.queryKey, sampleId),
    })
  }

  // Preserve the legacy all-samples behavior for callers that do not have a
  // concrete sample identifier. Normal annotation completion always supplies
  // one and takes the exhaustive predicate above.
  const invalidations = [
    queryClient.invalidateQueries({ queryKey: ["variants"] }),
    queryClient.invalidateQueries({ queryKey: ["variants-count"] }),
    queryClient.invalidateQueries({ queryKey: ["variants-total-count"] }),
    queryClient.invalidateQueries({ queryKey: ["variants-qc-stats"] }),
    queryClient.invalidateQueries({ queryKey: ["variants-chromosomes"] }),
    queryClient.invalidateQueries({ queryKey: ["findings-summary"] }),
    queryClient.invalidateQueries({ queryKey: ["findings"] }),
  ]
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
