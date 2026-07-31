/** React Query hooks and utilities for the report builder API (P4-10). */

import { useMutation, useQuery } from "@tanstack/react-query"

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
        const text = await res.text().catch(() => "")
        throw new Error(`Report generation failed: ${res.status}${text ? ` - ${text}` : ""}`)
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
    const text = await res.text().catch(() => "")
    throw new Error(`Report preview failed: ${res.status}${text ? ` - ${text}` : ""}`)
  }
  return res.text()
}

// ── FHIR R4 DiagnosticReport export (P4-12a) ────────────────────────

export interface FhirExportRequest {
  sample_id: number
  include_all?: boolean
}

export interface FhirExportEligibility {
  exportable: boolean
  max_observations: number
  observation_count: number | null
  reason: "too_large" | "no_annotated_variants" | null
}

/** Verify the actual FHIR Observation selection before enabling export. */
export function useFhirExportEligibility(sampleId: number | null) {
  return useQuery<FhirExportEligibility>({
    queryKey: ["fhir-export-eligibility", sampleId],
    enabled: sampleId != null,
    queryFn: async () => {
      const params = new URLSearchParams({
        sample_id: String(sampleId),
        include_all: "true",
      })
      const res = await fetch(`/api/export/fhir/eligibility?${params}`)
      if (!res.ok) {
        throw new Error(`FHIR export eligibility failed: ${res.status}`)
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
        const text = await res.text().catch(() => "")
        throw new Error(`FHIR export failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.blob()
    },
  })
}
