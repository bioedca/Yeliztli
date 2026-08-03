/** React Query hooks for the /api/individuals routes
 * (Step 48 / IND-04; Plan §9.2, §9.3, §9.5).
 *
 * Cache key convention:
 *   ["individuals"]                 — list
 *   ["individuals", id]             — detail for a single individual
 *
 * Mutations invalidate ["individuals"] (list) and, where applicable, the
 * specific detail key. Link / unlink also invalidate ["samples"] because
 * `samples.individual_id` is part of the sample row.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query"
import { readApiResponseBody } from "@/api/errors"

import type {
  IndividualCreate,
  IndividualDetail,
  IndividualSummary,
  IndividualUpdate,
  MergeCommitRequest,
  MergeCommitResponse,
  MergePreviewRequest,
  MergePreviewResponse,
} from "@/types/individuals"
import { IndividualsApiError } from "@/types/individuals"

async function parseError(res: Response, fallback: string): Promise<never> {
  const body = await readApiResponseBody(res)
  throw new IndividualsApiError(res.status, fallback, body)
}

export const individualsKeys = {
  all: ["individuals"] as const,
  list: () => ["individuals"] as const,
  detail: (id: number | null | undefined) => ["individuals", id] as const,
}

/** List individuals with summary (sample count, vendors, last activity). */
export function useIndividuals(
  options?: Omit<UseQueryOptions<IndividualSummary[]>, "queryKey" | "queryFn">,
) {
  return useQuery<IndividualSummary[]>({
    queryKey: individualsKeys.list(),
    queryFn: async () => {
      const res = await fetch("/api/individuals")
      if (!res.ok) await parseError(res, "Failed to fetch individuals")
      return (await res.json()) as IndividualSummary[]
    },
    ...options,
  })
}

/** Full detail for a single individual, including linked samples + aggregated count. */
export function useIndividual(
  individualId: number | null | undefined,
  options?: Omit<UseQueryOptions<IndividualDetail>, "queryKey" | "queryFn" | "enabled">,
) {
  return useQuery<IndividualDetail>({
    queryKey: individualsKeys.detail(individualId),
    queryFn: async () => {
      const res = await fetch(`/api/individuals/${individualId}`)
      if (!res.ok) await parseError(res, "Failed to fetch individual")
      return (await res.json()) as IndividualDetail
    },
    enabled: individualId != null,
    ...options,
  })
}

export function useCreateIndividual() {
  const qc = useQueryClient()
  return useMutation<IndividualDetail, IndividualsApiError, IndividualCreate>({
    mutationFn: async (body) => {
      const res = await fetch("/api/individuals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (!res.ok) await parseError(res, "Failed to create individual")
      return (await res.json()) as IndividualDetail
    },
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: individualsKeys.list() })
      qc.setQueryData(individualsKeys.detail(created.id), created)
    },
  })
}

export function useUpdateIndividual() {
  const qc = useQueryClient()
  return useMutation<
    IndividualDetail,
    IndividualsApiError,
    { individualId: number; data: IndividualUpdate }
  >({
    mutationFn: async ({ individualId, data }) => {
      const res = await fetch(`/api/individuals/${individualId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      })
      if (!res.ok) await parseError(res, "Failed to update individual")
      return (await res.json()) as IndividualDetail
    },
    onSuccess: (updated, variables) => {
      qc.invalidateQueries({ queryKey: individualsKeys.list() })
      qc.setQueryData(individualsKeys.detail(variables.individualId), updated)
    },
  })
}

export function useDeleteIndividual() {
  const qc = useQueryClient()
  return useMutation<void, IndividualsApiError, number>({
    mutationFn: async (individualId) => {
      const res = await fetch(`/api/individuals/${individualId}`, {
        method: "DELETE",
      })
      if (!res.ok) await parseError(res, "Failed to delete individual")
    },
    onSuccess: (_void, individualId) => {
      qc.invalidateQueries({ queryKey: individualsKeys.list() })
      qc.removeQueries({ queryKey: individualsKeys.detail(individualId) })
      // sample.individual_id values flip to NULL — refresh sample lists.
      qc.invalidateQueries({ queryKey: ["samples"] })
    },
  })
}

export function useLinkSample() {
  const qc = useQueryClient()
  return useMutation<
    IndividualDetail,
    IndividualsApiError,
    { individualId: number; sampleId: number }
  >({
    mutationFn: async ({ individualId, sampleId }) => {
      const res = await fetch(`/api/individuals/${individualId}/link-sample`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sample_id: sampleId }),
      })
      if (!res.ok) await parseError(res, "Failed to link sample")
      return (await res.json()) as IndividualDetail
    },
    onSuccess: (detail, variables) => {
      qc.invalidateQueries({ queryKey: individualsKeys.list() })
      qc.setQueryData(individualsKeys.detail(variables.individualId), detail)
      qc.invalidateQueries({ queryKey: ["samples"] })
      qc.invalidateQueries({ queryKey: ["samples", variables.sampleId] })
    },
  })
}

export function useUnlinkSample() {
  const qc = useQueryClient()
  return useMutation<
    IndividualDetail,
    IndividualsApiError,
    { individualId: number; sampleId: number }
  >({
    mutationFn: async ({ individualId, sampleId }) => {
      const res = await fetch(`/api/individuals/${individualId}/unlink-sample`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sample_id: sampleId }),
      })
      if (!res.ok) await parseError(res, "Failed to unlink sample")
      return (await res.json()) as IndividualDetail
    },
    onSuccess: (detail, variables) => {
      qc.invalidateQueries({ queryKey: individualsKeys.list() })
      qc.setQueryData(individualsKeys.detail(variables.individualId), detail)
      qc.invalidateQueries({ queryKey: ["samples"] })
      qc.invalidateQueries({ queryKey: ["samples", variables.sampleId] })
    },
  })
}

/** Dry-run the wizard preview step (Plan §10.6).
 *
 * Calls `POST /api/individuals/{id}/merge/preview`; no rows written. The
 * mutation lives alongside the link/unlink hooks so the wizard can re-fire
 * the preview when the user toggles strategy without recomputing client-
 * side state. */
export function useMergePreview() {
  return useMutation<
    MergePreviewResponse,
    IndividualsApiError,
    { individualId: number; data: MergePreviewRequest }
  >({
    mutationFn: async ({ individualId, data }) => {
      const res = await fetch(
        `/api/individuals/${individualId}/merge/preview`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        },
      )
      if (!res.ok) await parseError(res, "Failed to preview merge")
      return (await res.json()) as MergePreviewResponse
    },
  })
}

/** Commit the merge (Plan §10.6) — materialises the merged sample DB and
 * returns `{merged_sample_id, job_id}`. `job_id` may be empty when the
 * service's enqueue branch fell through to its warning path; the wizard
 * handles that by surfacing a re-annotate CTA. */
export function useMergeCommit() {
  const qc = useQueryClient()
  return useMutation<
    MergeCommitResponse,
    IndividualsApiError,
    { individualId: number; data: MergeCommitRequest }
  >({
    mutationFn: async ({ individualId, data }) => {
      const res = await fetch(`/api/individuals/${individualId}/merge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      })
      if (!res.ok) await parseError(res, "Failed to merge samples")
      return (await res.json()) as MergeCommitResponse
    },
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: individualsKeys.list() })
      qc.invalidateQueries({
        queryKey: individualsKeys.detail(variables.individualId),
      })
      // New `samples` row + `samples.individual_id` updated — refresh both.
      qc.invalidateQueries({ queryKey: ["samples"] })
    },
  })
}
