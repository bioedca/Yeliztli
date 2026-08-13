/** React Query hooks for sample management and file ingestion (P1-13, P1-16). */

import {
  readApiResponseBody,
  throwApiError,
} from "@/api/errors"

import {
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query"
import type {
  MergedChild,
  Sample,
  SampleUpdate,
} from "@/types/samples"
import type {
  ConcordanceReportResponse,
  MergeProvenanceResponse,
  MigrateFromSourcesResponse,
  StaleSampleDetail,
} from "@/types/individuals"
import { postIngestFile } from "@/api/setup"

export function useSamples() {
  return useQuery({
    queryKey: ["samples"],
    queryFn: async (): Promise<Sample[]> => {
      const res = await fetch("/api/samples")
      if (!res.ok) throw new Error("Failed to fetch samples")
      return await res.json()
    },
    staleTime: 0,
  })
}

export function useSample(sampleId: number | null) {
  return useQuery({
    queryKey: ["samples", sampleId],
    queryFn: async (): Promise<Sample> => {
      const res = await fetch(`/api/samples/${sampleId}`)
      if (!res.ok) throw new Error("Failed to fetch sample")
      return await res.json()
    },
    enabled: sampleId != null,
  })
}

export function useIngestFile() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: postIngestFile,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["samples"] })
    },
  })
}

export function useUpdateSample() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      sampleId,
      data,
    }: {
      sampleId: number
      data: SampleUpdate
    }): Promise<Sample> => {
      const res = await fetch(`/api/samples/${sampleId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      })
      if (!res.ok) {
        await throwApiError(res, "Failed to update sample")
      }
      return await res.json()
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["samples"] })
      queryClient.invalidateQueries({
        queryKey: ["samples", variables.sampleId],
      })
    },
  })
}

export function useDeleteSample() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (sampleId: number): Promise<void> => {
      const res = await fetch(`/api/samples/${sampleId}`, {
        method: "DELETE",
      })
      if (!res.ok) {
        await throwApiError(res, "Failed to delete sample")
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["samples"] })
    },
  })
}

/** API error surfaced by the concordance-report + merge-provenance hooks.
 *
 * Carries the HTTP status + decoded body so the ConcordanceReport page can
 * branch on 423 (stale sample, per Plan §7.5) vs 404 (missing resource)
 * without having to re-parse the response. */
export class SamplesApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, message: string, body: unknown) {
    super(message)
    this.name = "SamplesApiError"
    this.status = status
    this.body = body
  }

  /** `true` when this is the `require_fresh_sample` 423 from Plan §7.5. */
  isStaleSample(): this is SamplesApiError & { body: { detail: StaleSampleDetail } } {
    if (this.status !== 423) return false
    const body = this.body as { detail?: unknown } | null
    return (
      !!body &&
      typeof body === "object" &&
      typeof body.detail === "object" &&
      body.detail !== null
    )
  }
}

async function parseSamplesError(
  res: Response,
  fallback: string,
): Promise<never> {
  const body = await readApiResponseBody(res)
  throw new SamplesApiError(res.status, fallback, body)
}

/** Fetch the merged sample's provenance row (Plan §10.6). Returns `null` when
 * an existing sample is not merged; 404 when missing; 423 when stale. */
export function useMergeProvenance(sampleId: number | null) {
  return useQuery<MergeProvenanceResponse | null, SamplesApiError>({
    queryKey: ["samples", sampleId, "merge-provenance"],
    queryFn: async () => {
      const res = await fetch(`/api/samples/${sampleId}/merge-provenance`)
      if (!res.ok) await parseSamplesError(res, "Failed to fetch merge provenance")
      return (await res.json()) as MergeProvenanceResponse | null
    },
    enabled: sampleId != null,
    // Provenance is immutable once the merged sample is materialised.
    staleTime: Infinity,
    retry: false,
  })
}

/** Paginated concordance report for a merged sample (Plan §10.6). The
 * backend caps `limit` at 500 and returns 422 on out-of-range values, so
 * the page is responsible for keeping its requested limit within that
 * window. Same-sample placeholder data keeps the table mounted while a new
 * page loads, but is withheld across sample navigation so one sample's rows
 * can never render beneath another sample's provenance. */
export function useConcordanceReport(
  sampleId: number | null,
  limit: number,
  offset: number,
) {
  return useQuery<ConcordanceReportResponse, SamplesApiError>({
    queryKey: ["samples", sampleId, "concordance-report", limit, offset],
    queryFn: async () => {
      const params = new URLSearchParams({
        limit: String(limit),
        offset: String(offset),
      })
      const res = await fetch(
        `/api/samples/${sampleId}/concordance-report?${params.toString()}`,
      )
      if (!res.ok) {
        await parseSamplesError(res, "Failed to fetch concordance report")
      }
      return (await res.json()) as ConcordanceReportResponse
    },
    enabled: sampleId != null,
    placeholderData: (previousData, previousQuery) =>
      previousQuery?.queryKey[1] === sampleId ? previousData : undefined,
    staleTime: Infinity,
    retry: false,
  })
}

/** Post-merge re-watch candidates for a merged sample (Plan §10.6 / §10.7;
 * Step 72 / MRG-13). Lists every `watched_variants` row from the merged
 * sample's source samples whose rsid is not present on the merged sample
 * — the rsid-collapse case carries the merged sample's chosen rsid in
 * `rsid_on_merged_or_null`, the source-private case carries `null`.
 *
 * The route is gated by `require_fresh_merged_sample`, so an in-flight
 * annotation cascade returns 423; the modal layers the SSE annotation
 * channel on top via `enabled` so it never fires until annotation
 * reports `status='complete'`. A 423 caught here is rendered as a
 * benign "still annotating" banner by the modal (race condition with
 * the SSE gate). */
export function useMigrateFromSources(
  mergedId: number | null,
  enabled: boolean,
) {
  return useQuery<MigrateFromSourcesResponse, SamplesApiError>({
    queryKey: ["samples", mergedId, "migrate-from-sources"],
    queryFn: async () => {
      const res = await fetch(
        `/api/samples/${mergedId}/watched-variants/migrate-from-sources`,
      )
      if (!res.ok) {
        await parseSamplesError(res, "Failed to fetch migration candidates")
      }
      return (await res.json()) as MigrateFromSourcesResponse
    },
    enabled: mergedId != null && enabled,
    staleTime: Infinity,
    retry: false,
  })
}

/** Merged samples that reference this sample as a source (Plan §10.8 / Step 66).
 *
 * Returns ``[]`` when the sample has never been merged. The delete
 * confirmation hook uses this to surface the cascade count + names before
 * the user commits.
 */
export function useSampleMergedChildren(sampleId: number | null) {
  return useQuery({
    queryKey: ["samples", sampleId, "merged-children"],
    queryFn: async (): Promise<MergedChild[]> => {
      const res = await fetch(`/api/samples/${sampleId}/merged-children`)
      if (!res.ok) throw new Error("Failed to fetch merged children")
      return (await res.json()) as MergedChild[]
    },
    enabled: sampleId != null,
    staleTime: 0,
  })
}
