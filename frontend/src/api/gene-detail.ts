/** React Query hooks for gene detail API (P3-41, P3-42). */

import { throwApiError } from "@/api/errors"

import { useQuery } from "@tanstack/react-query"
import type { GeneDetailResponse } from "@/types/gene-detail"

async function fetchGeneDetail(
  symbol: string,
  sampleId: number,
): Promise<GeneDetailResponse> {
  const params = new URLSearchParams({ sample_id: String(sampleId) })
  const res = await fetch(`/api/genes/${encodeURIComponent(symbol)}?${params}`)
  if (!res.ok) {
    await throwApiError(res, `Gene detail fetch failed. Please try again.`)
  }
  return res.json()
}

/**
 * Fetch full gene detail (UniProt, phenotypes, literature, variants, AF).
 * staleTime: Infinity — annotation data is immutable once loaded.
 */
export function useGeneDetail(symbol: string | null, sampleId: number | null) {
  return useQuery({
    queryKey: ["gene-detail", symbol, sampleId],
    queryFn: () => fetchGeneDetail(symbol!, sampleId!),
    enabled: symbol != null && sampleId != null,
    staleTime: Infinity,
  })
}
