/** Cross-source PGx evidence API hook (SW-E2).
 *
 * Reads the context-only PharmGKB LoE / DPWG / FDA evidence layered over the
 * sample's CPIC prescribing alerts. Additive — a failure here must never block
 * the pharmacogenomics surface (the caller treats it as supplementary). */

import { useQuery } from "@tanstack/react-query"
import type { PgxGuidelinesResponse } from "@/types/pgxGuidelines"

async function getJson<T>(url: string, label: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) {
    const text = await res.text().catch(() => "")
    throw new Error(`${label} failed: ${res.status}${text ? ` - ${text}` : ""}`)
  }
  return res.json()
}

export function usePgxGuidelines(sampleId: number | null) {
  return useQuery({
    queryKey: ["pgx-guidelines", sampleId],
    queryFn: () =>
      getJson<PgxGuidelinesResponse>(
        `/api/analysis/pgx-guidelines?sample_id=${sampleId}`,
        "PGx guidelines",
      ),
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}
