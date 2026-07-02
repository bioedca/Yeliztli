/** HLA (imputed) module API hooks (Wave D). Read-only: HLA calls are computed
 * offline (scripts/predict_hla.py) and stored, so the views only GET + interpret. */

import { useQuery } from "@tanstack/react-query"
import type { HlaDrugHypersensitivityResponse } from "@/types/hla"

export function useHlaDrugHypersensitivity(sampleId: number | null) {
  return useQuery({
    queryKey: ["hla-drug-hypersensitivity", sampleId],
    queryFn: async (): Promise<HlaDrugHypersensitivityResponse> => {
      const res = await fetch(`/api/hla/drug-hypersensitivity?sample_id=${sampleId}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`HLA drug-hypersensitivity failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}
