/** React Query hook for the GRCh38 liftover batch (#2029).
 *
 * - useBatchLiftover: POST /api/liftover/{sample_id} → BatchLiftoverStats
 *
 * The Variant Explorer's GRCh38 columns are filled by this batch and nothing
 * else. Until #2029 no code path called it, so the columns were blank for every
 * variant of every sample. The batch is cheap (~5 s for a 677k-variant sample,
 * of which ~2 s is the conversion itself) and idempotent — it only reads rows
 * whose `chrom_grch38` is still NULL — so the toggle can trigger it directly
 * rather than needing a job queue or a progress stream.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { throwApiError } from "@/api/errors"

export interface BatchLiftoverStats {
  total: number
  converted: number
  failed: number
  already_lifted: number
}

/** Compute and store GRCh38 coordinates for every annotated variant of a sample.
 *
 * `onSampleSucceeded` / `onSampleFailed` are the *mutation's* own handlers, not
 * callbacks passed to an individual `mutate` call. That distinction is
 * load-bearing: a component holds one mutation observer, so a second `mutate`
 * detaches it from the first request, and per-call callbacks then never run for
 * that first request. Switching samples while a batch is in flight is exactly
 * that case, and the caller's per-sample bookkeeping has to settle for every
 * request it started — otherwise the abandoned sample stays marked "already
 * requested" and shows blank columns with no failure and no way to retry.
 */
export function useBatchLiftover({
  onSampleSucceeded,
  onSampleFailed,
  onSampleRefreshFailed,
}: {
  onSampleSucceeded?: (sampleId: number) => void
  onSampleFailed?: (sampleId: number) => void
  onSampleRefreshFailed?: (sampleId: number) => void
} = {}) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (sampleId: number): Promise<BatchLiftoverStats> => {
      const res = await fetch(`/api/liftover/${sampleId}`, { method: "POST" })
      if (!res.ok) {
        await throwApiError(res, "Unable to compute GRCh38 coordinates. Please try again.")
      }
      return await res.json()
    },
    onSuccess: async (_stats, sampleId) => {
      // Cached variant pages were fetched before the batch wrote the GRCh38
      // columns, so they still hold the NULLs. Without this the user enables
      // the toggle, the batch succeeds, and the columns stay blank until
      // something else happens to refetch.
      //
      // Awaited rather than fire-and-forget: `invalidateQueries` resolves only
      // once the refetch settles, and until then the table is still rendering
      // the pre-liftover NULLs. Reporting the batch finished before that hands
      // the user blank cells while the tooltip tells them a blank cell means an
      // unmappable position — the same "blanks presented as final" defect the
      // failure notice exists to prevent, one step later in the chain.
      //
      // Writing the coordinates straight into the cache would skip the round
      // trip, but the batch response carries counts only (total / converted /
      // failed / already_lifted), not per-variant coordinates, so there is
      // nothing to write; returning 677k coordinates just so the client could
      // patch its own cache is a far worse trade than one refetch.
      //
      // Both non-default options below were read out of the installed
      // @tanstack/query-core 5.90.20 rather than assumed
      // (build/modern/queryClient.js, `invalidateQueries` / `refetchQueries`):
      //
      // `refetchType: "all"` — the default is
      // `filters?.refetchType ?? filters?.type ?? "active"`, so an *inactive*
      // query is marked invalid and never fetched. Switching samples mid-batch
      // makes the first sample's query inactive, and the awaited invalidation
      // would then resolve having refreshed nothing: the pending state clears,
      // and returning to that sample renders the cached pre-liftover NULLs with
      // nothing to say they are about to change.
      //
      // `throwOnError` — `refetchQueries` swallows failures
      // (`if (!fetchOptions.throwOnError) promise = promise.catch(noop)`), so
      // without it a refetch that never delivered the coordinates is
      // indistinguishable from one that did.
      try {
        await queryClient.invalidateQueries(
          { queryKey: ["variants", sampleId], refetchType: "all" },
          { throwOnError: true },
        )
      } catch {
        // The batch itself succeeded and the coordinates are stored; only the
        // view is stale. Reporting that as a liftover failure would point the
        // user at "retry the computation" when the computation is done and it
        // is the fetch that needs retrying.
        onSampleRefreshFailed?.(sampleId)
        return
      }
      onSampleSucceeded?.(sampleId)
    },
    onError: (_error, sampleId) => {
      onSampleFailed?.(sampleId)
    },
  })
}
