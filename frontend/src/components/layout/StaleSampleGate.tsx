/** Full-page gate that blocks analysis pages when the active sample is stale.
 *
 * Probes a representative gated endpoint (`/api/variants/count`) for the
 * URL-scoped sample. When the route returns HTTP 423 the gate parses the
 * payload (`installed_version`, `required_version`, `update_url`,
 * `reannotate_url` — Plan §7.5) and renders a full-page banner whose single
 * CTA uses the canonical `POST /api/annotation/{sample_id}` escape hatch,
 * rather than trusting an advertised action URL. While re-annotation is
 * active, the gate reconnects through `/api/annotation/active/{sample_id}`
 * and polls the staleness probe until the backend authoritatively unlocks it.
 * Any other status — 2xx, 4xx other than 423, network error — lets `children`
 * through; this gate is concerned only with the staleness contract and never
 * blocks on unrelated failures.
 */

import { useEffect, useRef, type ReactNode } from 'react'
import { useMatch, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import {
  annotationActiveQueryKey,
  invalidateAnnotationResultQueries,
  useActiveAnnotationJob,
  type ActiveAnnotationJob,
  type AnnotationJobResult,
} from '@/api/annotation'
import { parseSampleId } from '@/lib/format'
import { cn } from '@/lib/utils'

export interface StalenessPayload {
  installed_version: string
  required_version: string
  update_url: string
  reannotate_url: string
}

interface StaleSampleGateProps {
  children: ReactNode
}

interface ReannotationRequest {
  sampleId: number
}

class ReannotationRequestError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ReannotationRequestError'
    this.status = status
  }
}

const sampleStalenessQueryKey = (sampleId: number | null) =>
  ['sample-staleness', sampleId] as const

function reannotationUrl(sampleId: number): string {
  return `/api/annotation/${sampleId}`
}

function isStalenessPayload(value: unknown): value is StalenessPayload {
  if (!value || typeof value !== 'object') return false
  const payload = value as Record<string, unknown>
  return (
    typeof payload.installed_version === 'string' &&
    typeof payload.required_version === 'string' &&
    typeof payload.update_url === 'string' &&
    typeof payload.reannotate_url === 'string'
  )
}

async function probeStaleness(sampleId: number): Promise<StalenessPayload | null> {
  const res = await fetch(`/api/variants/count?sample_id=${sampleId}`)
  if (res.status !== 423) return null
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null
  if (isStalenessPayload(body?.detail)) return body.detail

  // Keep the stale route fenced even if an intermediary strips the structured
  // payload. The action endpoint is stable, and this avoids rendering an
  // arbitrary 423 response to the user.
  return {
    installed_version: 'an earlier version',
    required_version: 'the current version',
    update_url: '',
    reannotate_url: `/api/annotation/${sampleId}`,
  }
}

export default function StaleSampleGate({ children }: StaleSampleGateProps) {
  const [searchParams] = useSearchParams()
  const concordanceMatch = useMatch('/samples/:id/concordance')
  const activeSampleId =
    parseSampleId(concordanceMatch?.params.id ?? null) ??
    parseSampleId(searchParams.get('sample_id'))
  const queryClient = useQueryClient()
  const activeJobQuery = useActiveAnnotationJob(activeSampleId)
  const activeJob = activeJobQuery.data ?? null
  const trackedJobRef = useRef<{ sampleId: number; jobId: string } | null>(null)

  const { data: stale, isPending: isStalenessPending } = useQuery<
    StalenessPayload | null
  >({
    queryKey: sampleStalenessQueryKey(activeSampleId),
    queryFn: async () => {
      const sampleId = activeSampleId as number
      const queryKey = sampleStalenessQueryKey(sampleId)
      const previous = queryClient.getQueryData<StalenessPayload | null>(queryKey)
      const next = await probeStaleness(sampleId)

      if (previous && !next) {
        await invalidateAnnotationResultQueries(queryClient, sampleId)
      }

      return next
    },
    enabled: activeSampleId != null,
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: activeJob ? 1_000 : false,
  })

  const reannotate = useMutation<
    AnnotationJobResult,
    ReannotationRequestError,
    ReannotationRequest
  >({
    mutationFn: async ({ sampleId }) => {
      const res = await fetch(reannotationUrl(sampleId), { method: 'POST' })
      if (!res.ok) {
        throw new ReannotationRequestError(
          res.status,
          'Unable to start re-annotation. Please try again.',
        )
      }
      return res.json() as Promise<AnnotationJobResult>
    },
    onSuccess: (result) => {
      const activeResult: ActiveAnnotationJob = {
        ...result,
        progress_pct: 0,
        message: 'Queued for annotation',
      }
      trackedJobRef.current = { sampleId: result.sample_id, jobId: result.job_id }
      queryClient.setQueryData(annotationActiveQueryKey(result.sample_id), activeResult)
      void queryClient.invalidateQueries({
        queryKey: annotationActiveQueryKey(result.sample_id),
      })
      void queryClient.invalidateQueries({
        queryKey: sampleStalenessQueryKey(result.sample_id),
      })
    },
    onError: (error, request) => {
      if (error.status !== 409) return
      void Promise.all([
        queryClient.invalidateQueries({
          queryKey: annotationActiveQueryKey(request.sampleId),
        }),
        queryClient.invalidateQueries({
          queryKey: sampleStalenessQueryKey(request.sampleId),
        }),
      ])
    },
  })
  const resetReannotation = reannotate.reset

  useEffect(() => {
    resetReannotation()
    trackedJobRef.current = null
    // Reset the mutation banner state when the active sample changes so
    // a prior success/error toast from a different sample doesn't leak in.
  }, [activeSampleId, resetReannotation])

  const activeJobId = activeJob?.job_id ?? null
  useEffect(() => {
    if (activeSampleId == null) {
      trackedJobRef.current = null
      return
    }

    if (activeJobId) {
      trackedJobRef.current = { sampleId: activeSampleId, jobId: activeJobId }
      return
    }

    const trackedJob = trackedJobRef.current
    if (
      trackedJob?.sampleId === activeSampleId &&
      !activeJobQuery.isPending &&
      !activeJobQuery.isFetching
    ) {
      trackedJobRef.current = null
      resetReannotation()
      void queryClient.invalidateQueries({
        queryKey: sampleStalenessQueryKey(activeSampleId),
      })
    }
  }, [
    activeJobId,
    activeJobQuery.isFetching,
    activeJobQuery.isPending,
    activeSampleId,
    queryClient,
    resetReannotation,
  ])

  // While an active sample's staleness probe is still pending, hold back
  // children so potentially-stale content never flashes. Also wait for the
  // initial active-job check so a reload cannot briefly expose a duplicate CTA.
  if (
    activeSampleId != null &&
    (isStalenessPending || activeJobQuery.isPending)
  ) {
    return null
  }

  if (!stale) {
    return <>{children}</>
  }

  const isConflict =
    reannotate.isError &&
    reannotate.error instanceof ReannotationRequestError &&
    reannotate.error.status === 409
  const isReconnecting = isConflict && activeJobQuery.isFetching && !activeJob
  const progressPct = Math.min(100, Math.max(0, activeJob?.progress_pct ?? 0))

  const banner = (
    <section
      aria-labelledby="stale-sample-gate-title"
      data-testid="stale-sample-gate"
      className="p-6 max-w-3xl mx-auto"
    >
      <div
        className={cn(
          'flex flex-col gap-4 rounded-lg border p-6',
          'border-amber-200 bg-amber-50 text-amber-900',
          'dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100',
        )}
      >
        <div className="flex items-start gap-3">
          <AlertTriangle className="h-6 w-6 shrink-0 mt-0.5" aria-hidden="true" />
          <div className="space-y-1">
            <h2 id="stale-sample-gate-title" className="text-base font-semibold">
              Sample requires re-annotation
            </h2>
            <p className="text-sm">
              This sample was annotated against bundle{' '}
              <strong data-testid="stale-installed-version">{stale.installed_version}</strong>;
              re-annotate against{' '}
              <strong data-testid="stale-required-version">{stale.required_version}</strong>{' '}
              to view results.
            </p>
          </div>
        </div>

        {reannotate.isError && !isConflict ? (
          <p
            role="alert"
            data-testid="stale-error"
            className="text-sm text-red-700 dark:text-red-300"
          >
            {reannotate.error instanceof Error
              ? reannotate.error.message
              : 'Re-annotation failed.'}
          </p>
        ) : null}

        {isConflict ? (
          <p
            role="status"
            data-testid="stale-reconnect-status"
            className="text-sm text-amber-800 dark:text-amber-200"
          >
            Re-annotation is already running — tracking progress…
          </p>
        ) : null}

        {activeJob ? (
          <div data-testid="stale-annotation-progress" className="space-y-2">
            <div
              role="status"
              aria-live="polite"
              className="flex items-center justify-between gap-3 text-sm"
            >
              <span>{activeJob.message || 'Re-annotation is in progress…'}</span>
              <span className="shrink-0 tabular-nums">{progressPct.toFixed(1)}%</span>
            </div>
            <div
              role="progressbar"
              aria-label="Re-annotation progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progressPct}
              className="h-2 w-full overflow-hidden rounded-full bg-amber-200 dark:bg-amber-900"
            >
              <div
                className="h-full rounded-full bg-amber-600 transition-[width] duration-300 dark:bg-amber-400"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            data-testid="stale-reannotate-cta"
            disabled={reannotate.isPending || Boolean(activeJob) || isReconnecting}
            onClick={() => {
              if (activeSampleId == null) return
              reannotate.mutate({
                sampleId: activeSampleId,
              })
            }}
            className={cn(
              'inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium',
              'bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-60',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
              'dark:bg-amber-500 dark:hover:bg-amber-400 dark:text-amber-950',
            )}
          >
            <RefreshCw
              className={cn(
                'h-4 w-4',
                (reannotate.isPending || activeJob || isReconnecting) && 'animate-spin',
              )}
              aria-hidden="true"
            />
            {reannotate.isPending
              ? 'Starting re-annotation…'
              : activeJob
                ? 'Re-annotation in progress'
                : isReconnecting
                  ? 'Reconnecting to re-annotation…'
                  : 'Re-annotate sample'}
          </button>
          {stale.update_url ? (
            <a
              href={stale.update_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm underline hover:no-underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary rounded"
            >
              View bundle update
            </a>
          ) : null}
        </div>
      </div>
    </section>
  )

  return banner
}
