/** React Query hooks for vcfanno overlay API (P4-12). */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import type {
  OverlayListResponse,
  OverlayUploadResponse,
  OverlayParsePreviewResponse,
  OverlayApplyResponse,
  OverlayResultsResponse,
} from "@/types/overlays"

/** List all saved overlay configs. Cached with 1-hour staleTime. */
export function useOverlays() {
  return useQuery({
    queryKey: ["overlays"],
    queryFn: async (): Promise<OverlayListResponse> => {
      const res = await fetch("/api/overlays")
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Failed to load overlays: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    staleTime: 1000 * 60 * 60, // 1 hour
  })
}

/** Parse an overlay file for preview (without saving). */
export function useParseOverlayPreview() {
  return useMutation({
    mutationFn: async (file: File): Promise<OverlayParsePreviewResponse> => {
      const formData = new FormData()
      formData.append("file", file)
      const res = await fetch("/api/overlays/parse", {
        method: "POST",
        body: formData,
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Parse failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
  })
}

/** Upload and save an overlay. */
export function useUploadOverlay() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      file,
      name,
      description,
    }: {
      file: File
      name: string
      description?: string
    }): Promise<OverlayUploadResponse> => {
      const formData = new FormData()
      formData.append("file", file)
      const params = new URLSearchParams({ name })
      if (description) params.set("description", description)
      const res = await fetch(`/api/overlays/upload?${params}`, {
        method: "POST",
        body: formData,
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Upload failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["overlays"] })
    },
  })
}

/** Delete an overlay. */
export function useDeleteOverlay() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (overlayId: number): Promise<void> => {
      const res = await fetch(`/api/overlays/${overlayId}`, { method: "DELETE" })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Delete failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
    },
    onSuccess: (_data, overlayId) => {
      queryClient.invalidateQueries({ queryKey: ["overlays"] })
      queryClient.invalidateQueries({ queryKey: ["overlay", overlayId] })
      queryClient.invalidateQueries({ queryKey: ["overlay-results", overlayId] })
    },
  })
}

/** Apply an overlay to a sample. */
export function useApplyOverlay() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      overlayId,
      sampleId,
    }: {
      overlayId: number
      sampleId: number
    }): Promise<OverlayApplyResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId) })
      const res = await fetch(`/api/overlays/${overlayId}/apply?${params}`, {
        method: "POST",
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Apply failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["overlay-results", variables.overlayId, variables.sampleId],
      })
    },
  })
}

/** Get overlay results for a specific overlay on a sample. */
export function useOverlayResults(overlayId: number | null, sampleId: number | null) {
  return useQuery({
    queryKey: ["overlay-results", overlayId, sampleId],
    queryFn: async (): Promise<OverlayResultsResponse> => {
      const params = new URLSearchParams({ sample_id: String(sampleId) })
      const res = await fetch(`/api/overlays/${overlayId}/results?${params}`)
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Failed to load results: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    enabled: overlayId != null && sampleId != null,
    staleTime: Infinity,
  })
}
