/** React Query hooks for custom gene panel API (P4-11). */

import { throwApiError } from "@/api/errors"

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import type {
  CustomPanelListResponse,
  PanelUploadResponse,
} from "@/types/custom-panels"

/** List all saved custom gene panels. Cached with 1-hour staleTime. */
export function useCustomPanels() {
  return useQuery({
    queryKey: ["custom-panels"],
    queryFn: async (): Promise<CustomPanelListResponse> => {
      const res = await fetch("/api/panels")
      if (!res.ok) {
        await throwApiError(res, `Failed to load panels. Please try again.`)
      }
      return res.json()
    },
    staleTime: 1000 * 60 * 60, // 1 hour
  })
}

/** Upload and save a custom panel. */
export function useUploadPanel() {
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
    }): Promise<PanelUploadResponse> => {
      const formData = new FormData()
      formData.append("file", file)
      const params = new URLSearchParams({ name })
      if (description) params.set("description", description)
      const res = await fetch(`/api/panels/upload?${params}`, {
        method: "POST",
        body: formData,
      })
      if (!res.ok) {
        await throwApiError(res, `Upload failed. Please try again.`)
      }
      return res.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["custom-panels"] })
    },
  })
}

/** Delete a custom panel. */
export function useDeletePanel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (panelId: number): Promise<void> => {
      const res = await fetch(`/api/panels/${panelId}`, { method: "DELETE" })
      if (!res.ok) {
        await throwApiError(res, `Delete failed. Please try again.`)
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["custom-panels"] })
    },
  })
}
