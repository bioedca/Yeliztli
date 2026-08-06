/** React Query hooks and utilities for the report builder API (P4-10). */

import { throwApiError } from "@/api/errors"

import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query"

export interface ReportRequest {
  sample_id: number
  modules?: string[]
  title?: string
}

/**
 * Generate a PDF report and trigger browser download.
 * Calls POST /api/reports/generate with the selected modules.
 */
export function useGenerateReport() {
  return useMutation({
    mutationFn: async (request: ReportRequest): Promise<Blob> => {
      const res = await fetch("/api/reports/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      })
      if (!res.ok) {
        await throwApiError(res, `Report generation failed. Please try again.`)
      }
      return res.blob()
    },
  })
}

/**
 * Fetch an HTML preview of the report.
 * Calls POST /api/reports/preview and returns the raw HTML string.
 */
export async function fetchReportPreview(request: ReportRequest): Promise<string> {
  const res = await fetch("/api/reports/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  })
  if (!res.ok) {
    await throwApiError(res, `Report preview failed. Please try again.`)
  }
  return res.text()
}

// ── FHIR R4 DiagnosticReport export (P4-12a) ────────────────────────

export interface FhirExportRequest {
  sample_id: number
  include_all?: boolean
  /** Report modules to scope the bundle to. Omit for the full-sample
   * selection; an empty array exports nothing. */
  modules?: string[]
}

export interface FhirExportEligibility {
  exportable: boolean
  max_observations: number
  observation_count: number | null
  reason: "too_large" | "no_annotated_variants" | null
}

/** Sort so a selection is keyed and requested by its members, not by the order
 * the user happened to click them — otherwise the same scope produces different
 * cache entries and re-requests an answer already held. */
function normaliseModules(modules: string[] | undefined): string[] | undefined {
  return modules ? [...new Set(modules)].sort() : undefined
}

/** Verify the actual FHIR Observation selection before enabling export.
 *
 * `modules` must match what the export will send, or eligibility answers a
 * different question than the one the button acts on. */
export function useFhirExportEligibility(
  sampleId: number | null,
  modules?: string[],
  { enabled = true }: { enabled?: boolean } = {},
) {
  const scoped = normaliseModules(modules)
  return useQuery<FhirExportEligibility>({
    queryKey: ["fhir-export-eligibility", sampleId, scoped ?? null],
    // Callers hold this until their selection exists. Asking about an empty
    // selection the user never made spends a request whose answer is discarded
    // the moment the real selection arrives.
    enabled: sampleId != null && enabled,
    // Every checkbox toggle changes the key, and without this each toggle would
    // re-enter `isPending` and disable the button mid-interaction — the flicker
    // #2225 removed, reintroduced by scoping. Holding the previous answer means
    // the action can briefly reflect the scope the user just left; the server
    // re-checks and returns 413, so the worst case is a refused click rather
    // than an oversized export.
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const params = new URLSearchParams({
        sample_id: String(sampleId),
        include_all: "true",
      })
      for (const module of scoped ?? []) params.append("modules", module)
      const res = await fetch(`/api/export/fhir/eligibility?${params}`)
      if (!res.ok) {
        await throwApiError(res, `FHIR export eligibility check failed.`)
      }
      return res.json()
    },
  })
}

/**
 * Export a FHIR R4 Bundle (DiagnosticReport + Observations) and trigger
 * browser download. Calls POST /api/export/fhir.
 */
export function useExportFhir() {
  return useMutation({
    mutationFn: async (request: FhirExportRequest): Promise<Blob> => {
      const res = await fetch("/api/export/fhir", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      })
      if (!res.ok) {
        await throwApiError(res, `FHIR export failed. Please try again.`)
      }
      return res.blob()
    },
  })
}
