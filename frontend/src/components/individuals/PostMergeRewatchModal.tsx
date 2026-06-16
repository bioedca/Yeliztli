/** Post-merge VUS re-watch modal (Step 72 / MRG-13; Plan §10.6, §10.7).
 *
 * Surfaced on first render of a merged sample's dashboard. Lists every
 * `watched_variants` row from the merged sample's source samples whose
 * rsid is NOT on the merged sample — either because the rsid was
 * collapsed (different rsid carried at the same (chrom, pos)) or the
 * locus is private to that source.
 *
 * Flow per Plan §10.7:
 *   1. Subscribe to the merged sample's annotation SSE channel via
 *      `useAnnotationProgress(jobId)`. The migrate-from-sources route is
 *      gated by `require_fresh_merged_sample`, so we defer the fetch
 *      until SSE reports `status='complete'`.
 *   2. A 423 caught here (race condition: SSE reported complete but
 *      `annotation_state` upsert lagged) renders as a benign
 *      "still annotating" banner, NOT an error. The modal stays open.
 *   3. Per-row "Re-watch" fires `POST /api/watches` with
 *      `{sample_id: merged_sample_id, rsid: rsid_on_merged, notes: notes_on_source}`.
 *      Rows where the locus is private (rsid_on_merged_or_null === null)
 *      are listed for transparency but the button is disabled.
 *   4. Header "Re-watch all" batches the per-row mutations.
 *   5. Modal is dismissible; does NOT block dashboard rendering.
 */

import { useEffect, useState } from "react"
import { Loader2, X } from "lucide-react"
import { useMutation, useQueryClient } from "@tanstack/react-query"

import { useAnnotationProgress } from "@/api/annotation"
import { useMigrateFromSources, SamplesApiError } from "@/api/samples"
import type { MigrateFromSourcesCandidate } from "@/types/individuals"

interface PostMergeRewatchModalProps {
  mergedSampleId: number
  /** Annotation job id returned by `POST /api/individuals/{id}/merge`.
   * `null` when the merge service's enqueue branch fell through (Plan
   * §10.6) — in that case the modal cannot defer on SSE and stays
   * gated until the user manually re-annotates. */
  jobId: string | null
  onClose: () => void
}

type RewatchStatus =
  | { state: "idle" }
  | { state: "pending" }
  | { state: "success" }
  | { state: "error"; message: string }

interface RewatchPayload {
  sampleId: number
  rsid: string
  notes: string
}

async function postWatch(payload: RewatchPayload): Promise<void> {
  const res = await fetch("/api/watches", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sample_id: payload.sampleId,
      rsid: payload.rsid,
      notes: payload.notes,
    }),
  })
  if (!res.ok) {
    // 409 (already watched) is benign and idempotent — the watch already
    // exists on the merged sample, so treat it as success rather than
    // marking the row failed/retryable forever.
    if (res.status === 409) return
    const text = await res.text().catch(() => "")
    throw new Error(text || `Re-watch failed: ${res.status}`)
  }
}

function candidateKey(c: MigrateFromSourcesCandidate): string {
  return `${c.sample_id}:${c.rsid_on_source}`
}

function canRewatch(c: MigrateFromSourcesCandidate): boolean {
  return c.rsid_on_merged_or_null !== null
}

export function PostMergeRewatchModal({
  mergedSampleId,
  jobId,
  onClose,
}: PostMergeRewatchModalProps) {
  const queryClient = useQueryClient()
  const progress = useAnnotationProgress(jobId, mergedSampleId)

  // Annotation cascade is finished when SSE reports complete OR no jobId
  // was provided AND the user opened the modal manually. Plan §10.6
  // empty-jobId case: stay gated, surface a manual prompt.
  const annotationComplete = progress?.status === "complete"
  const annotationFailed =
    progress?.status === "failed" || progress?.status === "cancelled"

  const migrateQuery = useMigrateFromSources(
    mergedSampleId,
    annotationComplete,
  )

  const [perRowStatus, setPerRowStatus] = useState<
    Record<string, RewatchStatus>
  >({})

  // Close on Escape — same idiom as MergeWizard.
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose()
    }
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [onClose])

  const rewatchMutation = useMutation({
    mutationFn: async (candidate: MigrateFromSourcesCandidate) => {
      if (candidate.rsid_on_merged_or_null === null) {
        throw new Error("Locus is private to source — cannot re-watch.")
      }
      await postWatch({
        sampleId: mergedSampleId,
        rsid: candidate.rsid_on_merged_or_null,
        notes: candidate.notes_on_source,
      })
    },
    onMutate: (candidate) => {
      setPerRowStatus((prev) => ({
        ...prev,
        [candidateKey(candidate)]: { state: "pending" },
      }))
    },
    onSuccess: (_data, candidate) => {
      setPerRowStatus((prev) => ({
        ...prev,
        [candidateKey(candidate)]: { state: "success" },
      }))
      queryClient.invalidateQueries({
        queryKey: ["watched-variants", mergedSampleId],
      })
    },
    onError: (err, candidate) => {
      setPerRowStatus((prev) => ({
        ...prev,
        [candidateKey(candidate)]: {
          state: "error",
          message: err instanceof Error ? err.message : "Re-watch failed",
        },
      }))
    },
  })

  const handleRewatchAll = async () => {
    const candidates = migrateQuery.data?.candidates ?? []
    const toRewatch = candidates.filter(
      (c) =>
        canRewatch(c) &&
        perRowStatus[candidateKey(c)]?.state !== "success",
    )
    for (const c of toRewatch) {
      try {
        await rewatchMutation.mutateAsync(c)
      } catch {
        // Per-row error already captured in onError — continue with the next.
      }
    }
  }

  // ── Render branches ──────────────────────────────────────────────────

  let body: React.ReactNode
  let footerActions: React.ReactNode = null

  if (jobId !== null && !annotationComplete && !annotationFailed) {
    body = (
      <p
        className="text-sm text-muted-foreground inline-flex items-center gap-2"
        data-testid="rewatch-modal-annotating"
      >
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Waiting for the
        merged sample's annotation to finish…
      </p>
    )
  } else if (jobId !== null && annotationFailed) {
    body = (
      <p
        className="text-sm text-destructive"
        role="alert"
        data-testid="rewatch-modal-annotation-failed"
      >
        Annotation did not complete — re-watching is unavailable until
        annotation succeeds.
      </p>
    )
  } else if (jobId === null && !annotationComplete) {
    // Service's enqueue branch fell through (empty job_id). Plan §10.6
    // tells the wizard to surface a manual re-annotate CTA; mirror that
    // semantics here.
    body = (
      <p
        className="text-sm text-muted-foreground"
        data-testid="rewatch-modal-no-job"
      >
        Annotation has not been scheduled yet for this merged sample.
        Re-watch candidates become available once annotation completes.
      </p>
    )
  } else if (migrateQuery.isPending) {
    body = (
      <p
        className="text-sm text-muted-foreground inline-flex items-center gap-2"
        data-testid="rewatch-modal-loading"
      >
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading
        re-watch candidates…
      </p>
    )
  } else if (migrateQuery.error) {
    if (
      migrateQuery.error instanceof SamplesApiError &&
      migrateQuery.error.status === 423
    ) {
      body = (
        <p
          className="text-sm text-muted-foreground"
          role="status"
          data-testid="rewatch-modal-stale-banner"
        >
          Merged sample is still annotating — refresh in a moment.
        </p>
      )
    } else {
      body = (
        <p
          className="text-sm text-destructive"
          role="alert"
          data-testid="rewatch-modal-error"
        >
          {migrateQuery.error.message}
        </p>
      )
    }
  } else if ((migrateQuery.data?.candidates.length ?? 0) === 0) {
    body = (
      <p
        className="text-sm text-muted-foreground"
        data-testid="rewatch-modal-empty"
      >
        No watched variants from the source samples need to be re-watched
        — every source watch is present on the merged sample.
      </p>
    )
  } else {
    const candidates = migrateQuery.data!.candidates
    const rewatchableCount = candidates.filter(canRewatch).length
    const allRewatched =
      rewatchableCount > 0 &&
      candidates.every(
        (c) =>
          !canRewatch(c) ||
          perRowStatus[candidateKey(c)]?.state === "success",
      )
    footerActions = (
      <button
        type="button"
        onClick={handleRewatchAll}
        disabled={rewatchMutation.isPending || allRewatched}
        className="px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 inline-flex items-center gap-1.5"
        data-testid="rewatch-modal-rewatch-all"
      >
        {rewatchMutation.isPending && (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        )}
        Re-watch all ({rewatchableCount})
      </button>
    )
    body = (
      <CandidateTable
        candidates={candidates}
        perRowStatus={perRowStatus}
        rewatchPending={rewatchMutation.isPending}
        onRewatch={(c) => rewatchMutation.mutate(c)}
      />
    )
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      data-testid="rewatch-modal-overlay"
    >
      <div
        className="bg-card border border-border rounded-lg shadow-xl w-full max-w-2xl mx-4"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rewatch-modal-title"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h3 id="rewatch-modal-title" className="font-medium text-sm">
            Re-watch source-sample variants
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="p-1 text-muted-foreground hover:text-foreground"
            aria-label="Close"
            data-testid="rewatch-modal-close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="px-4 py-3 space-y-3">
          <p className="text-xs text-muted-foreground">
            Variant watches do not propagate across merges. These watches
            from the source samples are not yet active on the merged
            sample.
          </p>
          {body}
        </div>
        <div className="flex justify-end gap-2 px-4 py-3 border-t border-border">
          {footerActions}
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-sm rounded-md border border-input bg-background hover:bg-accent text-foreground"
            data-testid="rewatch-modal-dismiss"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  )
}

function CandidateTable({
  candidates,
  perRowStatus,
  rewatchPending,
  onRewatch,
}: {
  candidates: MigrateFromSourcesCandidate[]
  perRowStatus: Record<string, RewatchStatus>
  rewatchPending: boolean
  onRewatch: (c: MigrateFromSourcesCandidate) => void
}) {
  return (
    <table
      className="w-full text-sm"
      data-testid="rewatch-modal-candidate-table"
    >
      <thead>
        <tr className="text-xs text-muted-foreground border-b border-border">
          <th className="text-left py-1.5">Source rsID</th>
          <th className="text-left py-1.5">Locus</th>
          <th className="text-left py-1.5">Merged rsID</th>
          <th className="text-left py-1.5">Notes</th>
          <th className="text-right py-1.5">Action</th>
        </tr>
      </thead>
      <tbody>
        {candidates.map((c) => {
          const key = candidateKey(c)
          const status = perRowStatus[key] ?? { state: "idle" }
          const rewatchable = canRewatch(c)
          return (
            <tr
              key={key}
              className="border-b border-border last:border-b-0"
              data-testid={`rewatch-row-${key}`}
            >
              <td className="py-1.5 font-mono text-xs">{c.rsid_on_source}</td>
              <td className="py-1.5 font-mono text-xs">
                {c.chrom}:{c.pos.toLocaleString()}
              </td>
              <td className="py-1.5 font-mono text-xs">
                {c.rsid_on_merged_or_null ?? (
                  <span className="text-muted-foreground">private</span>
                )}
              </td>
              <td className="py-1.5 text-xs text-muted-foreground truncate max-w-[14rem]">
                {c.notes_on_source}
              </td>
              <td className="py-1.5 text-right">
                {status.state === "success" ? (
                  <span
                    className="text-xs text-emerald-700 dark:text-emerald-400"
                    data-testid={`rewatch-row-${key}-success`}
                  >
                    Re-watched
                  </span>
                ) : status.state === "error" ? (
                  <span
                    className="text-xs text-destructive"
                    role="alert"
                    title={status.message}
                    data-testid={`rewatch-row-${key}-error`}
                  >
                    Failed
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => onRewatch(c)}
                    disabled={!rewatchable || rewatchPending}
                    className="px-2 py-1 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 inline-flex items-center gap-1"
                    data-testid={`rewatch-row-${key}-button`}
                    title={
                      rewatchable
                        ? undefined
                        : "Locus is private to the source sample — cannot re-watch."
                    }
                  >
                    {status.state === "pending" && (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    )}
                    Re-watch
                  </button>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
