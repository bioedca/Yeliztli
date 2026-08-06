/** React Query hook for variant detail API (P2-20, P2-21). */

import { throwApiError } from "@/api/errors"

import { useQuery } from "@tanstack/react-query"
import type { VariantDetail } from "@/types/variant-detail"

async function fetchVariantDetail(
  rsid: string,
  sampleId: number,
): Promise<VariantDetail> {
  const params = new URLSearchParams({ sample_id: String(sampleId) })
  const res = await fetch(`/api/variants/${encodeURIComponent(rsid)}?${params}`)
  if (!res.ok) {
    await throwApiError(res, `Variant detail fetch failed. Please try again.`)
  }
  return res.json()
}

/**
 * Fetch full detail for a single variant by rsid.
 * staleTime: Infinity — variant annotation data is immutable once loaded.
 */
export function useVariantDetail(rsid: string | null, sampleId: number | null) {
  return useQuery({
    queryKey: ["variant-detail", rsid, sampleId],
    queryFn: () => fetchVariantDetail(rsid!, sampleId!),
    enabled: rsid != null && sampleId != null,
    staleTime: Infinity,
  })
}
