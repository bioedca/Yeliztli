/** HLA (imputed) module API hooks (Wave D). Read-only: HLA calls are computed
 * offline (scripts/predict_hla.py) and stored, so the views only GET + interpret. */

import { useQuery } from "@tanstack/react-query"
import type {
  HlaDrugHypersensitivityResponse,
  HlaRuleOutsResponse,
  HlaSusceptibilityResponse,
  HlaViewerResponse,
} from "@/types/hla"

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

export function useHlaRuleOuts(sampleId: number | null) {
  return useQuery({
    queryKey: ["hla-rule-outs", sampleId],
    queryFn: async (): Promise<HlaRuleOutsResponse> => {
      const res = await fetch(`/api/hla/rule-outs?sample_id=${sampleId}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`HLA rule-outs failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

export function useHlaSusceptibility(sampleId: number | null) {
  return useQuery({
    queryKey: ["hla-susceptibility", sampleId],
    queryFn: async (): Promise<HlaSusceptibilityResponse> => {
      const res = await fetch(`/api/hla/susceptibility?sample_id=${sampleId}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`HLA susceptibility failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}

export function useHlaAlleles(sampleId: number | null) {
  return useQuery({
    queryKey: ["hla-alleles", sampleId],
    queryFn: async (): Promise<HlaViewerResponse> => {
      const res = await fetch(`/api/hla/alleles?sample_id=${sampleId}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`HLA alleles failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: Infinity,
  })
}
