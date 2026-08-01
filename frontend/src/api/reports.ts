/** React Query hooks and utilities for the report builder API (P4-10). */

import { throwApiError } from "@/api/errors"

import { useMutation } from "@tanstack/react-query"

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
